"""`cardledger-db generate` writes the game catalogs to a local folder; `cardledger-db upload` copies them to the bucket.

Run from the repository root: `corrections/` is read relative to the working directory.
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from . import corrections
from .catalog import assemble, catalog_key, encode
from .http import make_client
from .matcher import match
from .model import GAMES, Enrichment, GameCatalog, SourceError
from .sources import cardmarket, lorcanajson, mtgjson, openrift, punkrecords, tcgdex, yamlyugi

logger = logging.getLogger("cardledger_database")


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


def generate(slugs: list[str], out: Path, cache_dir: Path, client) -> list[str]:
    """Writes each game to `out`; returns the games that failed (their previous file, if any, is left as it was)."""
    failed = []
    for slug in slugs:
        start = time.monotonic()
        try:
            catalog = generate_game(slug, client, cache_dir)
            path = out / catalog_key(slug)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encode(catalog))
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


def upload(slugs: list[str], out: Path, s3=None) -> list[str]:
    """Copies the games' files from `out` to the bucket under the same keys; returns the keys uploaded."""
    bucket = os.environ.get("CARDLEDGER_S3_BUCKET")
    if not bucket:
        raise SourceError("CARDLEDGER_S3_BUCKET is not set")
    files = {catalog_key(slug): out / catalog_key(slug) for slug in slugs}
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise SourceError(f"nothing to upload, run generate first: {', '.join(missing)}")
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
    return list(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cardledger-db")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help in (("generate", "generate the game catalogs into the output folder"),
                       ("upload", "upload the generated catalogs to the S3 bucket")):
        command = commands.add_parser(name, help=help)
        command.add_argument("--game", action="append", choices=list(GAMES), help="only this game (default: all)")
        command.add_argument("--out", type=Path, default=Path("out"), help="output folder (default: out)")
        if name == "generate":
            command.add_argument("--cache", type=Path, default=Path(".cache"), help="cache folder (default: .cache)")
    args = parser.parse_args(argv)
    slugs = args.game or list(GAMES)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        if args.command == "generate":
            with make_client() as client:
                return 1 if generate(slugs, args.out, args.cache, client) else 0
        load_dotenv(Path(".env"))  # the working directory's .env, without overriding the environment
        try:
            upload(slugs, args.out)
        except SourceError as exc:
            logger.error("%s", exc)
            return 1
        return 0
    finally:
        logger.removeHandler(handler)


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
