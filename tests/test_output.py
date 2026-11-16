import gzip
import json
from datetime import UTC, date, datetime

import pytest

from cardledger_database.model import GameCatalog, Product, SourceSet
from cardledger_database.output import (
    MANIFEST_KEY,
    catalog_key,
    decode,
    encode,
    encode_manifest,
    game_entry,
    read_manifest,
)


def _catalog():
    exps = [
        SourceSet("zzz", "Zed", None),
        SourceSet("aaa", "Alpha é", date(1993, 8, 5), "ja"),
    ]
    prods = [
        Product(20, "B", "zzz", "2", "rare", "u", "ul", 1, "Singles"),
        Product(10, "A é", "aaa", "1", "common", "u2", "ul2", 1, "Singles"),
        Product(5, "C", "aaa", "", "", "", "", 5, "Boosters"),
    ]
    return GameCatalog("mtg", exps, prods, 3)


def test_encode_decode_roundtrip():
    cat = _catalog()
    out = decode(encode(cat), "mtg")
    assert out.game == "mtg"
    assert out.skipped == 0
    assert out.expansions == sorted(cat.expansions, key=lambda e: e.code)
    assert out.products == sorted(cat.products, key=lambda p: (p.expansion_code, p.id_product))


def test_encoding_is_deterministic():
    a = _catalog()
    b = GameCatalog("mtg", list(reversed(a.expansions)), list(reversed(a.products)), 0)
    assert encode(a) == encode(b)
    assert encode(a) == encode(a)


def test_expansions_come_before_products():
    lines = [json.loads(x) for x in gzip.decompress(encode(_catalog())).decode("utf-8").splitlines()]
    types = [x["type"] for x in lines]
    assert types == ["expansion"] * 2 + ["product"] * 3
    assert lines[0]["code"] == "aaa" and lines[0]["released_at"] == "1993-08-05"
    assert lines[1]["released_at"] is None
    assert [p["id_product"] for p in lines[2:]] == [5, 10, 20]


def test_manifest():
    cat = _catalog()
    now = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
    data = encode(cat)
    entry = game_entry("mtg", data, cat, now)
    assert entry["key"] == catalog_key("mtg") == "catalog/v1/mtg.jsonl.gz"
    assert entry["products"] == 3 and entry["expansions"] == 2
    assert len(entry["sha256"]) == 64
    m = read_manifest(encode_manifest({"mtg": entry}, now))
    assert m["format_version"] == 1 and m["games"]["mtg"] == entry
    assert MANIFEST_KEY == "catalog/v1/manifest.json"


def test_read_manifest_missing_and_wrong_version():
    assert read_manifest(None) == {"format_version": 1, "generated_at": None, "games": {}}
    with pytest.raises(ValueError):
        read_manifest(b'{"format_version": 2, "games": {}}')
