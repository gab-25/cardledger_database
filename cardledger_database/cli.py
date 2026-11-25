"""`cardledger-db generate` writes the game catalogs and their recognition indexes to a local folder; `cardledger-db
upload` copies them to the bucket; `cardledger-db index` rebuilds only the indexes from the catalogs in the folder.

Run from the repository root: `corrections/` and `expansions/` are read relative to the working directory.
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

from . import corrections
from .catalog import assemble, catalog_key, encode
from .http import make_client
from .matcher import match
from .model import GAMES, Enrichment, GameCatalog, SourceError
from .sources import cardmarket, lorcanajson, mtgjson, openrift, punkrecords, tcgdex, yamlyugi

logger = logging.getLogger("cardledger_database")
VISION_CACHE = "vision"  # subfolder of the cache folder with the card images and their hashes


def _enrichment(slug: str, client, cache_dir: Path, products, singles) -> Enrichment:
    game = GAMES[slug]
    if game.source == "mtgjson":
        return mtgjson.fetch(client)
    if game.source == "tcgdex":
        return tcgdex.fetch(client, cache_dir)
    if game.source == "lorcanajson":
        return lorcanajson.fetch(client)
    if game.source == "openrift":
        return openrift.fetch(client)
    if game.source == "punkrecords":
        return punkrecords.fetch(client, products, singles)
    groups, printings = yamlyugi.fetch(client)
    return match(products, groups, printings, singles).enrichment


def generate_game(slug: str, client, cache_dir: Path) -> GameCatalog:
    """Cardmarket products -> source -> corrections -> assemble."""
    products, singles = cardmarket.fetch_products(client, GAMES[slug].cm_id)
    enrichment = _enrichment(slug, client, cache_dir, products, singles)
    enrichment, warnings = corrections.apply(corrections.load(slug), products, enrichment)
    for warning in warnings:
        logger.warning("%s: %s", slug, warning)
    return assemble(GAMES[slug], products, enrichment, singles)


def _write(path: Path, data: bytes) -> None:
    """Writes through a temporary file, so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _index(slug: str, data: bytes, cache_dir: Path, client) -> bytes:
    """The recognition index of the catalog `data`."""
    return build_index(slug, data, hashlib.sha256(data).hexdigest(), client, cache_dir / VISION_CACHE,
                       confirm_only=GAMES[slug].confirm_only)


def generate(slugs: list[str], out: Path, cache_dir: Path, client, index: bool = True) -> list[str]:
    """Writes each game's catalog and, with `index`, its recognition index to `out`; returns the games that failed
    (their previous files, if any, are left as they were)."""
    failed = []
    for slug in slugs:
        start = time.monotonic()
        try:
            catalog = generate_game(slug, client, cache_dir)
            data = encode(catalog)
            # The index is built before anything is written: a game that fails keeps its previous pair of files.
            index_data = _index(slug, data, cache_dir, client) if index else None
            path = out / catalog_key(slug)
            _write(path, data)
            if index_data is not None:
                _write(out / index_key(slug), index_data)
            with_image = sum(1 for p in catalog.products if p.image_url)
            logger.info(
                "%s: written %s products=%d enriched=%d expansions=%d with_image=%.1f%% skipped=%d seconds=%.0f",
                slug, path, len(catalog.products), catalog.enriched, len(catalog.expansions),
                100 * with_image / len(catalog.products) if catalog.products else 0, catalog.skipped,
                time.monotonic() - start,
            )
        except Exception as exc:
            failed.append(slug)
            logger.error("%s: failed after %.0fs: %s", slug, time.monotonic() - start, exc,
                         exc_info=not isinstance(exc, SourceError))
    return failed


def index(slugs: list[str], out: Path, cache_dir: Path, client) -> list[str]:
    """Rebuilds each game's recognition index from its catalog in `out`; returns the games that failed."""
    failed = []
    for slug in slugs:
        path = out / catalog_key(slug)
        try:
            if not path.is_file():
                raise SourceError(f"no catalog, run generate first: {path}")
            _write(out / index_key(slug), _index(slug, path.read_bytes(), cache_dir, client))
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


def upload(slugs: list[str], out: Path, s3=None) -> list[str]:
    """Copies the games' catalogs and indexes from `out` to the bucket under the same keys; returns the keys uploaded.

    Nothing is uploaded unless every catalog has its index, built from that same catalog."""
    bucket = os.environ.get("CARDLEDGER_S3_BUCKET")
    if not bucket:
        raise SourceError("CARDLEDGER_S3_BUCKET is not set")
    files = {catalog_key(slug): out / catalog_key(slug) for slug in slugs}
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise SourceError(f"nothing to upload, run generate first: {', '.join(missing)}")
    indexes = {index_key(slug): _check_index(slug, out / catalog_key(slug), out / index_key(slug)) for slug in slugs}
    if s3 is None:
        import boto3

        required = ("CARDLEDGER_S3_ACCESS_KEY_ID", "CARDLEDGER_S3_SECRET_ACCESS_KEY", "CARDLEDGER_S3_REGION")
        unset = [name for name in required if not os.environ.get(name)]
        if unset:
            raise SourceError(f"{', '.join(unset)} not set")
        s3 = boto3.client("s3", endpoint_url=os.environ.get("CARDLEDGER_S3_ENDPOINT") or None,
                          aws_access_key_id=os.environ["CARDLEDGER_S3_ACCESS_KEY_ID"],
                          aws_secret_access_key=os.environ["CARDLEDGER_S3_SECRET_ACCESS_KEY"],
                          region_name=os.environ["CARDLEDGER_S3_REGION"])
    for key, path in files.items():
        s3.put_object(Bucket=bucket, Key=key, Body=path.read_bytes(), ContentType="application/json")
        logger.info("uploaded s3://%s/%s", bucket, key)
    for key, data in indexes.items():
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType="application/octet-stream")
        logger.info("uploaded s3://%s/%s", bucket, key)
    return [*files, *indexes]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cardledger-db")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help in (("generate", "generate the game catalogs and their indexes into the output folder"),
                       ("index", "rebuild the indexes from the catalogs in the output folder"),
                       ("upload", "upload the generated catalogs and indexes to the S3 bucket")):
        command = commands.add_parser(name, help=help)
        command.add_argument("--game", action="append", choices=list(GAMES), help="only this game (default: all)")
        command.add_argument("--out", type=Path, default=Path("out"), help="output folder (default: out)")
        if name in ("generate", "index"):
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
            with make_client() as client:
                return 1 if index(slugs, args.out, args.cache, client) else 0
        load_dotenv(Path(".env"))  # the working directory's .env, without overriding the environment
        try:
            upload(slugs, args.out)
        except SourceError as exc:
            logger.error("%s", exc)
            return 1
        return 0
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
