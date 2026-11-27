"""Sealed products (boosters, boxes, decks...) written by hand in sealed/<game>.toml."""

import re
from pathlib import Path

from cardledger_database.corrections import _read
from cardledger_database.model import Row, sealed_id

CATEGORIES = ("booster", "booster_box", "bundle", "case", "deck", "other")
_KEYS = {"key": str, "name": str, "set_code": str, "category": str, "image_source": str}
_REQUIRED = ("name", "set_code", "category")
_KEY = re.compile(r"[a-z0-9][a-z0-9-]*")


def load(game: str, root: Path = Path("sealed")) -> list[Row]:
    path = root / f"{game}.toml"
    rows = []
    seen: set[str] = set()
    for entry in _read(path, "product", _KEYS, "key"):
        if not _KEY.fullmatch(entry["key"]):
            raise ValueError(f"{path}: key {entry['key']!r} must be lower-case letters, digits and '-'")
        if entry["key"] in seen:
            raise ValueError(f"{path}: key {entry['key']!r} used twice")
        seen.add(entry["key"])
        for field in _REQUIRED:
            if field not in entry:
                raise ValueError(f"{path}: [[product]] {entry['key']!r} without {field}")
        if entry["category"] not in CATEGORIES:
            raise ValueError(f"{path}: category {entry['category']!r} must be one of {', '.join(CATEGORIES)}")
        rows.append(
            Row(
                id=sealed_id(game, entry["key"]),
                kind="sealed",
                name=entry["name"],
                set_code=entry["set_code"].lower(),
                number="",
                variant="",
                rarity="",
                languages=(),
                finishes=(),
                category=entry["category"],
                image_source=entry.get("image_source", ""),
            )
        )
    return rows
