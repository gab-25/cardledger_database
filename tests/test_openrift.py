from datetime import date

import httpx
import pytest

from cardledger_database.model import CardSet, SourceError
from cardledger_database.sources import openrift

SETS = [
    {"id": "s-ogn", "slug": "OGN", "name": "Origins",
     "releases": {"SC": {"releasedAt": "2025-08-01"}, "EN": {"releasedAt": "2025-10-31"}}},
    {"id": "s-sfd", "slug": "SFD", "name": "Spiritforged", "releases": {"SC": {"releasedAt": "2026-01-09"}}},
    {"id": "s-new", "slug": "NEW", "name": "Not Out Yet", "releases": {}},
]
RARITIES = [{"slug": "common", "label": "Common"}, {"slug": "epic", "label": "Epic"}]
CARDS = {"c1": {"name": "Annie, Fiery"}, "c2": {"name": "Jinx"}}


def printing(short_code, set_id="s-ogn", language="EN", finish="normal", markers=(), images=("img-1",),
             art="normal", rarity="epic", rank=1, signed=False, card="c1"):
    return {"shortCode": short_code, "setId": set_id, "rarity": rarity, "artVariant": art, "isSigned": signed,
            "isOvernumbered": False, "markers": list(markers), "finish": finish,
            "images": [{"imageId": i} for i in images], "language": language, "canonicalRank": rank, "cardId": card}


def catalog(printings):
    return {"sets": SETS, "cards": CARDS, "printings": printings, "enums": {"rarities": RARITIES}}


def client(payload=None, status=200, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(str(request.url))
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def fetch(printings):
    return openrift.fetch(client(catalog(printings)))


def by_id(data):
    return {r.id: r for r in data.rows}


def large(image_id):
    return openrift.IMAGE_URL.format(id=image_id, size="full")


GROUP = {
    "fr": printing("OGN-007a", language="FR", images=("fr",)),
    "foil": printing("OGN-007a", finish="foil", images=("foil",)),
    "plain": printing("OGN-007a", images=("plain",), rank=2),
    "alt": printing("OGN-007a", art="altart", images=("alt",)),
    "signed": printing("OGN-007a", signed=True, images=("signed",)),
    "promo": printing("OGN-007a", markers=("promo",), images=("promo",)),
}


def test_finishes_and_languages_of_a_group_are_one_row():
    r = by_id(fetch(GROUP))["riftbound:ogn:7A"]
    assert r.finishes == ("foil", "normal") and r.languages == ("en", "fr")
    assert r.image_source == large("plain")
    assert (r.kind, r.name, r.set_code, r.number, r.variant, r.rarity, r.category, r.source_key) == (
        "card", "Annie, Fiery", "ogn", "7A", "", "Epic", "", "plain")


def test_alt_art_and_signed_are_their_own_rows():
    rows = by_id(fetch(GROUP))
    assert set(rows) == {"riftbound:ogn:7A", "riftbound:ogn:7A:altart", "riftbound:ogn:7A:signed",
                         "riftbound:ogn:7A:promo"}
    assert rows["riftbound:ogn:7A:altart"].image_source == large("alt")
    assert rows["riftbound:ogn:7A:signed"].image_source == large("signed")


def test_variant_joins_non_default_parts_in_a_stable_order():
    p = {"a": printing("OGN-001", art="altart", signed=True, markers=("summoner", "promo"))}
    assert set(by_id(fetch(p))) == {"riftbound:ogn:1:altart-signed-promo-summoner"}


def test_finish_names_are_normalized():
    p = {"a": printing("OGN-001", finish="shatter-foil"), "b": printing("OGN-001", finish="metal-deluxe")}
    assert by_id(fetch(p))["riftbound:ogn:1"].finishes == ("metal_deluxe", "shatter_foil")


def test_no_links_request():
    requests = []
    openrift.fetch(client(catalog({"p1": printing("OGN-001")}), requests=requests))
    assert requests == [openrift.CATALOG_URL]


@pytest.mark.parametrize("short_code, number", [
    ("OGN-007a", "7A"), ("UNL-229*", "229*"), ("VEN-R04", "R04"), ("UNL-T01", "T01"), ("RAD-SP1", "SP1"),
    ("OGN", ""),
])
def test_number_is_the_short_code_after_the_set(short_code, number):
    assert fetch({"p1": printing(short_code)}).rows[0].number == number


def test_group_takes_its_english_plain_printing():
    p = {"sc": printing("OGN-001", language="SC", images=("sc",), card="c2"),
         "foil": printing("OGN-001", finish="foil", images=("foil",), card="c2"),
         "plain": printing("OGN-001", images=("plain",), rank=2)}
    r = fetch(p).rows[0]
    assert (r.image_source, r.name, r.source_key) == (large("plain"), "Annie, Fiery", "plain")


def test_a_printing_with_an_image_beats_one_without():
    p = {"bare": printing("OGN-001", images=()), "sc": printing("OGN-001", language="SC", images=("sc",))}
    assert fetch(p).rows[0].image_source == large("sc")


def test_choice_does_not_depend_on_order():
    a = {"x": printing("OGN-001", images=("x",)), "y": printing("OGN-001", images=("y",))}
    assert fetch(a).rows == fetch(dict(reversed(a.items()))).rows


def test_row_without_image_and_unknown_rarity():
    r = fetch({"p1": printing("OGN-001", images=(), rarity="mythic")}).rows[0]
    assert (r.rarity, r.image_source) == ("mythic", "")


def test_sets_with_english_release_date_else_earliest():
    assert fetch({}).sets == [
        CardSet("ogn", "Origins", date(2025, 10, 31)),
        CardSet("sfd", "Spiritforged", date(2026, 1, 9)),
        CardSet("new", "Not Out Yet", None),
    ]


@pytest.mark.parametrize("payload, status", [
    ({}, 500),
    ({"printings": {}}, 200),
    ({"sets": [], "cards": {}, "printings": [], "enums": {"rarities": []}}, 200),
    (catalog({"p1": printing("OGN-001", set_id="missing")}), 200),
    (catalog({"p1": printing("OGN-001", card="missing")}), 200),
])
def test_bad_payload_raises_source_error(payload, status):
    with pytest.raises(SourceError, match="OpenRift"):
        openrift.fetch(client(payload, status))


def test_not_json_raises_source_error():
    c = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"<html>")))
    with pytest.raises(SourceError, match="OpenRift"):
        openrift.fetch(c)
