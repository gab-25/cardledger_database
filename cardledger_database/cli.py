import argparse
import logging
import sys
from pathlib import Path

from . import generate
from .games import GAMES
from .http import make_client
from .store import LocalStore, store_from_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cardledger-db")
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate", help="generate, check and publish the game catalogs")
    gen.add_argument("--game", action="append", choices=list(GAMES), help="a game to generate (default: all)")
    gen.add_argument("--out", type=Path, help="write to this folder instead of the S3 bucket")
    args = parser.parse_args(argv)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    package = logging.getLogger("cardledger_database")
    package.addHandler(handler)
    package.setLevel(logging.INFO)
    try:
        store = LocalStore(args.out) if args.out else store_from_env()
        with make_client() as client:
            outcomes = generate.run(args.game or list(GAMES), store, client)
    finally:
        package.removeHandler(handler)
    return 0 if all(o in ("published", "unchanged") for o in outcomes.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
