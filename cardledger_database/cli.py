"""`cardledger-db generate` writes the game catalogs and their recognition indexes to a local folder, and the
catalogs' images to the cache's bucket folder; `cardledger-db upload` copies them to the bucket; `cardledger-db index`
rebuilds only the indexes from the catalogs in the folder.

Run from the repository root: `corrections/` and `sealed/` are read relative to the working directory.
"""

import argparse
import hashlib
import logging
import os
import sys
import time
from pathlib import Path

from cardledger_vision import Index, InvalidIndex, build_index, index_key
from dotenv import load_dotenv

from . import corrections, images, sealed
from .catalog import assemble, catalog_key, encode
from .http import make_client
from .images import ImageStats
from .model import GAMES, GameCatalog, SourceData, SourceError
from .sources import lorcanajson, openrift, punkrecords, scryfall, tcgdex, ygoprodeck

logger = logging.getLogger("cardledger_database")
VISION_CACHE = "vision"  # subfolder of the cache folder with the image hashes of the recognition index
IMAGE_CACHE_CONTROL = "public, max-age=31536000, immutable"  # an image's path changes when its bytes do


def _source(slug: str, client, cache_dir: Path) -> SourceData:
    source = GAMES[slug].source
    if source == "scryfall":
        return scryfall.fetch(client, cache_dir)
    if source == "tcgdex":
        return tcgdex.fetch(client, cache_dir)
    if source == "lorcanajson":
        return lorcanajson.fetch(client)
    if source == "punkrecords":
        return punkrecords.fetch(client, cache_dir)
    if source == "ygoprodeck":
        return ygoprodeck.fetch(client, cache_dir)
    if source == "openrift":
        return openrift.fetch(client)
    raise ValueError(f"unknown source {source!r}")


def generate_game(slug: str, client, cache_dir: Path) -> tuple[GameCatalog, ImageStats]:
    """Source -> corrections -> assemble (with the sealed products) -> images hosted in the cache's bucket folder."""
    data, warnings = corrections.apply(slug, corrections.load(slug), _source(slug, client, cache_dir))
    catalog, assemble_warnings = assemble(slug, data, sealed.load(slug))
    for warning in [*warnings, *assemble_warnings]:
        logger.warning("%s: %s", slug, warning)
    return images.host(slug, catalog, client, cache_dir)


def _write(path: Path, data: bytes) -> None:
    """Writes through a temporary file, so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _index(slug: str, data: bytes, cache_dir: Path) -> bytes:
    """The recognition index of the catalog `data`, from its images in the cache's bucket folder."""
    return build_index(slug, data, hashlib.sha256(data).hexdigest(), images.bucket_dir(cache_dir),
                       cache_dir / VISION_CACHE, confirm_only=GAMES[slug].confirm_only)


def generate(slugs: list[str], out: Path, cache_dir: Path, client, index: bool = True) -> list[str]:
    """Writes each game's catalog and, with `index`, its recognition index to `out`; returns the games that failed
    (their previous files, if any, are left as they were)."""
    failed = []
    for slug in slugs:
        start = time.monotonic()
        try:
            catalog, stats = generate_game(slug, client, cache_dir)
            data = encode(catalog)
            # The index is built before anything is written: a game that fails keeps its previous pair of files.
            index_data = _index(slug, data, cache_dir) if index else None
            path = out / catalog_key(slug)
            _write(path, data)
            if index_data is not None:
                _write(out / index_key(slug), index_data)
            sealed_rows = sum(1 for r in catalog.rows if r.kind == "sealed")
            with_image = sum(1 for r in catalog.rows if r.image)
            logger.info(
                "%s: written %s rows=%d cards=%d sealed=%d sets=%d merged=%d with_image=%.1f%% images_downloaded=%d "
                "images_failed=%d seconds=%.0f",
                slug, path, len(catalog.rows), len(catalog.rows) - sealed_rows, sealed_rows, len(catalog.sets),
                catalog.merged, 100 * with_image / len(catalog.rows) if catalog.rows else 0, stats.downloaded,
                stats.failed, time.monotonic() - start,
            )
        except Exception as exc:
            failed.append(slug)
            logger.error("%s: failed after %.0fs: %s", slug, time.monotonic() - start, exc,
                         exc_info=not isinstance(exc, SourceError))
    return failed


def index(slugs: list[str], out: Path, cache_dir: Path) -> list[str]:
    """Rebuilds each game's recognition index from its catalog in `out`; returns the games that failed."""
    failed = []
    for slug in slugs:
        path = out / catalog_key(slug)
        try:
            if not path.is_file():
                raise SourceError(f"no catalog, run generate first: {path}")
            _write(out / index_key(slug), _index(slug, path.read_bytes(), cache_dir))
            logger.info("%s: written %s", slug, out / index_key(slug))
        except Exception as exc:
            failed.append(slug)
            logger.error("%s: %s", slug, exc, exc_info=not isinstance(exc, SourceError))
    return failed


def _check_index(slug: str, catalog_path: Path, index_path: Path) -> bytes:
    """The index file's bytes, once it is known to be the index of this very catalog."""
    if not index_path.is_file():
        raise SourceError(f"nothing to upload, run generate or index first: {index_path}")
    data = index_path.read_bytes()
    try:
        header = Index.load(data).header
    except InvalidIndex as exc:
        raise SourceError(f"{index_path} is not a valid index: {exc}") from exc
    if header.game != slug:
        raise SourceError(f"{index_path} is the index of {header.game!r}, not {slug!r}")
    if header.catalog_sha256 != hashlib.sha256(catalog_path.read_bytes()).hexdigest():
        raise SourceError(f"{index_path} was built from another catalog than {catalog_path}: run index")
    return data


def _s3_client():
    """The S3 client of the CARDLEDGER_S3_* variables."""
    import boto3

    required = ("CARDLEDGER_S3_ACCESS_KEY_ID", "CARDLEDGER_S3_SECRET_ACCESS_KEY", "CARDLEDGER_S3_REGION")
    unset = [name for name in required if not os.environ.get(name)]
    if unset:
        raise SourceError(f"{', '.join(unset)} not set")
    return boto3.client("s3", endpoint_url=os.environ.get("CARDLEDGER_S3_ENDPOINT") or None,
                        aws_access_key_id=os.environ["CARDLEDGER_S3_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["CARDLEDGER_S3_SECRET_ACCESS_KEY"],
                        region_name=os.environ["CARDLEDGER_S3_REGION"])


def _listed(s3, bucket: str, prefix: str) -> set[str]:
    """Every key of the bucket under `prefix`, page after page."""
    keys: set[str] = set()
    token = None
    while True:
        page = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
        keys.update(obj["Key"] for obj in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        token = page["NextContinuationToken"]


def _upload_game(slug: str, out: Path, cache_dir: Path, s3, bucket: str) -> None:
    """Checks the game's files, then uploads the images the bucket lacks, the index and the catalog last, so a
    published catalog never points to a missing image or comes without its index."""
    catalog_path = out / catalog_key(slug)
    if not catalog_path.is_file():
        raise SourceError(f"nothing to upload, run generate first: {catalog_path}")
    catalog = catalog_path.read_bytes()
    index_data = _check_index(slug, catalog_path, out / index_key(slug))
    root = images.bucket_dir(cache_dir)
    needed = images.referenced(catalog)
    missing = sorted(key for key in needed if not (root / key).is_file())
    if missing:
        raise SourceError(f"{len(missing)} images of {catalog_path} not in {root}, run generate: {missing[0]}")
    there = needed & _listed(s3, bucket, images.image_prefix(slug))
    for key in sorted(needed - there):
        s3.put_object(Bucket=bucket, Key=key, Body=(root / key).read_bytes(), ContentType="image/webp",
                      CacheControl=IMAGE_CACHE_CONTROL)
    logger.info("%s: uploaded images=%d already_there=%d", slug, len(needed - there), len(there))
    s3.put_object(Bucket=bucket, Key=index_key(slug), Body=index_data, ContentType="application/octet-stream")
    logger.info("uploaded s3://%s/%s", bucket, index_key(slug))
    s3.put_object(Bucket=bucket, Key=catalog_key(slug), Body=catalog, ContentType="application/json")
    logger.info("uploaded s3://%s/%s", bucket, catalog_key(slug))


def upload(slugs: list[str], out: Path, cache_dir: Path, s3=None) -> list[str]:
    """Copies each game's images (from the cache's bucket folder), index and catalog (from `out`) to the bucket under
    the same keys; returns the games that failed. A game is uploaded only if its index was built from that same
    catalog and every image it references is in the cache; otherwise none of its files is."""
    bucket = os.environ.get("CARDLEDGER_S3_BUCKET")
    if not bucket:
        raise SourceError("CARDLEDGER_S3_BUCKET is not set")
    if s3 is None:
        s3 = _s3_client()
    failed = []
    for slug in slugs:
        try:
            _upload_game(slug, out, cache_dir, s3, bucket)
        except Exception as exc:
            failed.append(slug)
            logger.error("%s: %s", slug, exc, exc_info=not isinstance(exc, SourceError))
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cardledger-db")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help in (("generate", "generate the game catalogs and their indexes into the output folder"),
                       ("index", "rebuild the indexes from the catalogs in the output folder"),
                       ("upload", "upload the generated catalogs, indexes and images to the S3 bucket")):
        command = commands.add_parser(name, help=help)
        command.add_argument("--game", action="append", choices=list(GAMES), help="only this game (default: all)")
        command.add_argument("--out", type=Path, default=Path("out"), help="output folder (default: out)")
        command.add_argument("--cache", type=Path, default=Path(".cache"), help="cache folder (default: .cache)")
        if name == "generate":
            command.add_argument("--no-index", action="store_true",
                                 help="only the catalogs, without the recognition indexes (upload then refuses)")
    args = parser.parse_args(argv)
    slugs = args.game or list(GAMES)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    vision_logger = logging.getLogger("cardledger_vision")
    for log in (logger, vision_logger):
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    try:
        if args.command == "generate":
            with make_client() as client:
                return 1 if generate(slugs, args.out, args.cache, client, index=not args.no_index) else 0
        if args.command == "index":
            return 1 if index(slugs, args.out, args.cache) else 0
        load_dotenv(Path(".env"))  # the working directory's .env, without overriding the environment
        try:
            return 1 if upload(slugs, args.out, args.cache) else 0
        except SourceError as exc:
            logger.error("%s", exc)
            return 1
    finally:
        for log in (logger, vision_logger):
            log.removeHandler(handler)


if __name__ == "__main__":
    sys.exit(main())


def run() -> None:
    """The console entry point: `main`, with Ctrl+C ending the run at once (exit 130) and without a traceback.

    `os._exit` skips the interpreter's wait for the download threads still running, which otherwise blocks the
    exit and needs more Ctrl+C presses. Whatever had to be kept (the TCGdex cache) is already saved by then."""
    try:
        code = main()
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        code = 130
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
