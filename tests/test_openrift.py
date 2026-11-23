from datetime import date

import httpx
import pytest

from cardledger_database.model import SourceCard, SourceError, SourceSet
from cardledger_database.sources import openrift

SETS = [
    {"id": "s-ogn", "slug": "OGN", "name": "Origins",
     "releases": {"SC": {"releasedAt": "2025-08-01"}, "EN": {"releasedAt": "2025-10-31"}}},
    {"id": "s-sfd", "slug": "SFD", "name": "Spiritforged", "releases": {"SC": {"releasedAt": "2026-01-09"}}},
    {"id": "s-new", "slug": "NEW", "name": "Not Out Yet", "releases": {}},
]
RARITIES = [{"slug": "common", "label": "Common"}, {"slug": "epic", "label": "Epic"}]


def printing(short_code, set_id="s-ogn", language="EN", finish="normal", markers=(), images=("img-1",),
             art="normal", rarity="epic", rank=1):
    return {"shortCode": short_code, "setId": set_id, "rarity": rarity, "artVariant": art, "isSigned": False,
            "markers": list(markers), "finish": finish, "images": [{"imageId": i} for i in images],
            "language": language, "canonicalRank": rank}


def catalog(printings):
    return {"sets": SETS, "printings": printings, "enums": {"rarities": RARITIES}}


def links(ids: dict[str, int | None]):
    return {"links": {pid: {"cardmarket": {"available": True, "productId": cm} if cm else None,
                            "tcgplayer": {"available": True, "productId": 1}} for pid, cm in ids.items()}}


def client(catalog_payload, links_payload, status=200):
    def handler(request):
        if str(request.url) == openrift.CATALOG_URL:
            return httpx.Response(status, json=catalog_payload)
        assert str(request.url) == openrift.LINKS_URL
        return httpx.Response(status, json=links_payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def fetch(printings, ids):
    return openrift.fetch(client(catalog(printings), links(ids)))


def test_card_keyed_by_cardmarket_id():
    cards = fetch({"p1": printing("OGN-001", images=("abc",))}, {"p1": 9001}).cards
    assert cards == {9001: SourceCard("ogn", "001", "Epic", openrift.IMAGE_URL.format(id="abc", size="400w"),
                                      openrift.IMAGE_URL.format(id="abc", size="full"))}


def test_printing_without_cardmarket_id_is_ignored():
    cards = fetch({"p1": printing("OGN-001"), "p2": printing("OGN-002")}, {"p1": 9001, "p2": None}).cards
    assert set(cards) == {9001}


@pytest.mark.parametrize("short_code, number", [
    ("OGN-007a", "007a"), ("UNL-229*", "229*"), ("VEN-R04", "R04"), ("UNL-T01", "T01"), ("RAD-SP1", "SP1"),
    ("OGN", ""),
])
def test_number_is_the_short_code_after_the_set(short_code, number):
    assert fetch({"p1": printing(short_code)}, {"p1": 9001}).cards[9001].collector_number == number


def test_one_cardmarket_product_takes_its_english_plain_printing():
    printings = {
        "sc": printing("OGN-001", language="SC", images=("sc",)),
        "foil": printing("OGN-001", finish="foil", images=("foil",)),
        "promo": printing("OGN-001", markers=("promo",), images=("promo",)),
        "plain": printing("OGN-001", images=("plain",), rank=2),
        "alt": printing("OGN-001a", art="altart", images=("alt",)),
    }
    cards = fetch(printings, dict.fromkeys(printings, 9001)).cards
    assert cards[9001].image_url == openrift.IMAGE_URL.format(id="plain", size="400w")


def test_a_printing_with_an_image_beats_one_without():
    printings = {"bare": printing("OGN-001", images=()), "sc": printing("OGN-001", language="SC", images=("sc",))}
    cards = fetch(printings, dict.fromkeys(printings, 9001)).cards
    assert cards[9001].image_url == openrift.IMAGE_URL.format(id="sc", size="400w")


def test_choice_does_not_depend_on_order():
    a = {"x": printing("OGN-001", images=("x",)), "y": printing("OGN-001", images=("y",))}
    b = dict(reversed(a.items()))
    assert fetch(a, dict.fromkeys(a, 9001)).cards == fetch(b, dict.fromkeys(b, 9001)).cards


def test_card_without_image_and_unknown_rarity():
    card = fetch({"p1": printing("OGN-001", images=(), rarity="mythic")}, {"p1": 9001}).cards[9001]
    assert (card.rarity, card.image_url, card.image_large_url) == ("mythic", "", "")


def test_sets_with_english_release_date_else_earliest():
    sets = fetch({}, {}).sets
    assert sets == {
        "ogn": SourceSet("ogn", "Origins", date(2025, 10, 31)),
        "sfd": SourceSet("sfd", "Spiritforged", date(2026, 1, 9)),
        "new": SourceSet("new", "Not Out Yet", None),
    }


@pytest.mark.parametrize("catalog_payload, links_payload, status", [
    ({}, {}, 500),
    ({"printings": {}}, {"links": {}}, 200),
    ({"sets": [], "printings": [], "enums": {"rarities": []}}, {"links": {}}, 200),
    (catalog({"p1": printing("OGN-001", set_id="missing")}), links({"p1": 9001}), 200),
    (catalog({}), {"nolinks": {}}, 200),
])
def test_bad_payload_raises_source_error(catalog_payload, links_payload, status):
    with pytest.raises(SourceError, match="OpenRift"):
        openrift.fetch(client(catalog_payload, links_payload, status))


def test_not_json_raises_source_error():
    c = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"<html>")))
    with pytest.raises(SourceError, match="OpenRift"):
        openrift.fetch(c)
