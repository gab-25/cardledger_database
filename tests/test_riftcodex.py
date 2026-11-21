from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from cardledger_database.matcher import Group, Printing
from cardledger_database.model import SourceError
from cardledger_database.sources import riftcodex

SETS = [
    {"set_id": "UNL", "name": "Unleashed", "cardmarket_id": None, "published_on": "2026-05-08T00:00:00"},
    {"set_id": "OGN", "name": "Origins", "cardmarket_id": "6286", "published_on": "2025-10-31T00:00:00"},
]


def rc_card(name, set_id, number, label="Origins", rarity="Rare", riftbound_id=None, updated="2026-07-10T00:00:00"):
    return {"name": name, "collector_number": number, "classification": {"rarity": rarity},
            "riftbound_id": riftbound_id if riftbound_id is not None else f"{set_id.lower()}-{number:03d}-298", "metadata": {"updated_on": updated},
            "set": {"set_id": set_id, "label": label}, "media": {"image_url": f"https://cms/{set_id}{number}.png"}}


def test_parse_sets():
    assert riftcodex.parse_sets(SETS) == [Group(1, "Origins", "OGN", date(2025, 10, 31)),
                                          Group(2, "Unleashed", "UNL", date(2026, 5, 8))]


def test_parse_cards():
    groups = riftcodex.parse_sets(SETS)
    printings, groups = riftcodex.parse_cards([rc_card("Vi - Piltover Enforcer (Signature)", "OGN", 229),
                                               rc_card("Teemo", "PR", 3, label="Promos")], groups)
    assert printings == [
        Printing(1, 1, "Vi, Piltover Enforcer (Signature)", "229", "Rare", "https://cms/OGN229.png", "https://cms/OGN229.png"),
        Printing(2, 3, "Teemo", "003", "Rare", "https://cms/PR3.png", "https://cms/PR3.png"),
    ]
    assert groups[-1] == Group(3, "Promos", "PR", None)  # a set missing from /sets gets its own group


def pages_client(pages=3, fail_page=None, fail_status=500):
    seen = []

    def handler(request):
        url = urlparse(str(request.url))
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        seen.append((url.path, q))
        if url.path == "/sets":
            return httpx.Response(200, json={"items": SETS, "pages": 1})
        page = int(q["page"])
        if page == fail_page:
            return httpx.Response(fail_status)
        return httpx.Response(200, json={"items": [rc_card(f"Card {page}", "OGN", page)], "pages": pages})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_fetch_reads_every_page():
    c, seen = pages_client()
    groups, printings = riftcodex.fetch(c)
    pages = sorted(int(q["page"]) for path, q in seen if path == "/cards")
    assert pages == [1, 2, 3] and all(q["size"] == "100" for path, q in seen if path == "/cards")
    assert sorted(p.name for p in printings) == ["Card 1", "Card 2", "Card 3"]
    assert [g.abbreviation for g in groups] == ["OGN", "UNL"]


def test_failing_page_raises(sleeps):
    c, _ = pages_client(fail_page=2)
    with pytest.raises(SourceError, match="Riftcodex"):
        riftcodex.fetch(c)


def test_missing_page_raises():
    c, _ = pages_client(fail_page=3, fail_status=404)
    with pytest.raises(SourceError, match="Riftcodex"):
        riftcodex.fetch(c)


def test_bad_json_raises():
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"nope")))
    with pytest.raises(SourceError, match="Riftcodex"):
        riftcodex.fetch(c)


def test_fewer_cards_than_announced_raises():
    def handler(request):
        if request.url.path == "/sets":
            return httpx.Response(200, json={"items": SETS})
        return httpx.Response(200, json={"items": [rc_card("Card", "OGN", 1)], "pages": 1, "total": 2})

    with pytest.raises(SourceError, match="Riftcodex"):
        riftcodex.fetch(httpx.Client(transport=httpx.MockTransport(handler)))


def test_number_comes_from_the_riftbound_id():
    printings, _ = riftcodex.parse_cards([
        rc_card("Body Rune", "VEN", 4, riftbound_id="ven-r04"),
        rc_card("Dune Surfer", "VEN", 4, riftbound_id="ven-004-166"),
        rc_card("Sett, Brawler", "VEN", 4, riftbound_id="ven-sp4-006"),
        rc_card("Vi", "UNL", 229, riftbound_id="unl-229*-219"),
        rc_card("Odd", "UNL", 7, riftbound_id=""),
    ], [])
    assert [p.number for p in printings] == ["R04", "004", "SP4", "229*", "7"]


def test_duplicate_records_keep_the_latest():
    printings, _ = riftcodex.parse_cards([
        rc_card("Shen", "OGN", 5, riftbound_id="ogn-005-298", updated="2026-07-10T00:00:00", rarity="Common"),
        rc_card("Shen", "OGN", 5, riftbound_id="ogn-005-298", updated="2026-07-14T00:00:00", rarity="Rare"),
    ], [])
    assert [(p.number, p.rarity) for p in printings] == [("005", "Rare")]
