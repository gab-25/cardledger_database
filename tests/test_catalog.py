import json
from datetime import date

from cardledger_database.catalog import assemble, catalog_key, encode
from cardledger_database.model import CardSet, GameCatalog, Row, SourceData


def card(id, set_code="s", **kw):
    fields = dict(kind="card", name="Card", number="1", variant="", rarity="rare", languages=("en",),
                  finishes=("normal",), category="")
    return Row(id, set_code=set_code, **{**fields, **kw})


def sealed_row(id, set_code="s", **kw):
    return card(id, set_code, kind="sealed", name="Booster Box", number="", languages=(), finishes=(),
                category="booster_box", **kw)


def test_catalog_key():
    assert catalog_key("mtg") == "catalog/v1/mtg.json"


def test_assemble_merges_duplicates_deterministically():
    a = card("x:s:1", languages=("de",), finishes=("foil",), image_source="b", source_key="2")
    b = card("x:s:1", languages=("en",), finishes=("normal",), image_source="a", source_key="1")
    cat, warnings = assemble("x", SourceData([a, b], [CardSet("s", "S", None)]), [])
    (row,) = cat.rows
    assert row.image_source == "a"                   # first by source_key
    assert row.languages == ("de", "en") and row.finishes == ("foil", "normal")
    assert cat.merged == 1 and len(warnings) == 1 and "x:s:1" in warnings[0]


def test_assemble_drops_empty_sets_and_invents_missing_ones():
    cat, _ = assemble("x", SourceData([card("x:t:1", set_code="t")], [CardSet("s", "S", None)]), [])
    assert cat.sets == [CardSet("t", "T", None)]


def test_assemble_adds_sealed():
    cat, _ = assemble("x", SourceData([], []), [sealed_row("x:sealed:k", set_code="s")])
    assert [r.kind for r in cat.rows] == ["sealed"]


def test_encode_is_deterministic_and_well_shaped():
    cat, _ = assemble("x", SourceData([card("x:s:2"), card("x:s:1", image_source="http://src")],
                                      [CardSet("s", "S", date(2020, 1, 2), "ja")]), [])
    doc = json.loads(encode(cat))
    assert list(doc) == ["rows", "sets"]
    assert [r["id"] for r in doc["rows"]] == ["x:s:1", "x:s:2"]
    assert set(doc["rows"][0]) == {"id", "kind", "name", "set_code", "number", "variant", "rarity", "languages",
                                   "finishes", "category", "image", "image_large"}   # no image_source, no source_key
    assert doc["sets"] == [{"code": "s", "name": "S", "released_at": "2020-01-02", "region": "ja"}]
    assert encode(cat) == encode(cat)


def test_encode_caps_lengths():
    doc = json.loads(encode(GameCatalog("x", [], [card("x:s:1", name="n" * 300, rarity="r" * 60)])))
    assert len(doc["rows"][0]["name"]) == 200 and len(doc["rows"][0]["rarity"]) == 40
