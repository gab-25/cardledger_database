import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cardledger_database.errors import SourceError
from cardledger_database.sources import tcgdex
from cardledger_database.store import LocalStore

NOW = datetime(2026, 11, 16, tzinfo=UTC)
IMG = "https://assets.tcgdex.net/en/sv/sv08/001"


def card(cid, local, cm=100, image=IMG):
    c = {"id": cid, "localId": local, "name": "N", "rarity": "Common", "set": {"id": cid.split("-")[0]}}
    if image:
        c["image"] = image
    if cm:
        c["pricing"] = {"cardmarket": {"idProduct": cm}}
    return c


def make(routes):
    calls = []

    def handler(request):
        path = request.url.path.removeprefix("/v2")
        calls.append(path)
        if path not in routes:
            return httpx.Response(404)
        return httpx.Response(200, json=routes[path])

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def en_routes(**extra):
    routes = {
        "/en/sets": [{"id": "sv08", "name": "Surging Sparks"}],
        "/en/sets/sv08": {
            "id": "sv08", "name": "Surging Sparks", "releaseDate": "2024-11-08",
            "abbreviation": {"official": "SSP"}, "cards": [{"id": "sv08-001", "localId": "001"}],
        },
        "/en/cards/sv08-001": card("sv08-001", "001", cm=794256),
    }
    routes.update(extra)
    return routes


def test_cards_get_cardmarket_id_and_images(tmp_path):
    client, _ = make(en_routes())
    result = tcgdex.fetch(client, LocalStore(tmp_path), langs=("en",), now=NOW)
    c = result.cards[794256]
    assert (c.set_code, c.collector_number, c.rarity) == ("ssp", "1", "Common")
    assert c.image_url == IMG + "/low.webp" and c.image_large_url == IMG + "/high.webp"
    assert result.sets["ssp"].released_at.isoformat() == "2024-11-08"


def test_card_without_image_has_empty_urls(tmp_path):
    routes = en_routes(**{"/en/cards/sv08-001": card("sv08-001", "001", image=None)})
    client, _ = make(routes)
    c = tcgdex.fetch(client, LocalStore(tmp_path), langs=("en",), now=NOW).cards[100]
    assert c.image_url == "" and c.image_large_url == ""


def test_cached_cards_are_not_refetched(tmp_path):
    store = LocalStore(tmp_path)
    tcgdex.fetch(make(en_routes())[0], store, langs=("en",), now=NOW)
    client, calls = make(en_routes())
    result = tcgdex.fetch(client, store, langs=("en",), now=NOW + timedelta(days=30))
    assert "/en/cards/sv08-001" not in calls
    assert 794256 in result.cards


def test_cached_card_without_id_is_refetched_after_7_days(tmp_path):
    store = LocalStore(tmp_path)
    no_id = en_routes(**{"/en/cards/sv08-001": card("sv08-001", "001", cm=None)})
    assert tcgdex.fetch(make(no_id)[0], store, langs=("en",), now=NOW).cards == {}
    client, calls = make(en_routes())
    tcgdex.fetch(client, store, langs=("en",), now=NOW + timedelta(days=3))
    assert "/en/cards/sv08-001" not in calls
    result = tcgdex.fetch(client, store, langs=("en",), now=NOW + timedelta(days=8))
    assert "/en/cards/sv08-001" in calls and 794256 in result.cards


def test_set_code_is_official_abbreviation(tmp_path):
    routes = en_routes(**{"/en/sets/sv08": {"id": "sv08", "name": "S", "cards": []}})
    sets = tcgdex.fetch(make(en_routes())[0], LocalStore(tmp_path), langs=("en",), now=NOW).sets
    assert set(sets) == {"ssp"}
    sets = tcgdex.fetch(make(routes)[0], LocalStore(tmp_path / "x"), langs=("en",), now=NOW).sets
    assert set(sets) == {"sv08"} and sets["sv08"].released_at is None


def test_colliding_codes_fall_back_to_tcgdex_id(tmp_path):
    routes = {
        "/en/sets": [{"id": "a1"}, {"id": "b1"}],
        "/en/sets/a1": {"id": "a1", "name": "A", "abbreviation": {"official": "XX"}, "cards": []},
        "/en/sets/b1": {"id": "b1", "name": "B", "abbreviation": {"official": "xx"}, "cards": []},
    }
    sets = tcgdex.fetch(make(routes)[0], LocalStore(tmp_path), langs=("en",), now=NOW).sets
    assert set(sets) == {"a1", "b1"}


def test_japanese_sets_are_marked_ja(tmp_path):
    routes = en_routes(**{
        "/ja/sets": [{"id": "SV8"}],
        "/ja/sets/SV8": {"id": "SV8", "name": "超電ブレイカー", "cards": [{"id": "SV8-001", "localId": "001"}]},
        "/ja/cards/SV8-001": card("SV8-001", "001", cm=555),
    })
    result = tcgdex.fetch(make(routes)[0], LocalStore(tmp_path), now=NOW)
    assert result.sets["ssp"].language == "" and result.sets["sv8"].language == "ja"
    assert result.cards[555].set_code == "sv8"


def test_ja_id_colliding_with_en_gets_suffix(tmp_path):
    routes = {
        "/en/sets": [{"id": "sv8"}], "/en/sets/sv8": {"id": "sv8", "name": "E", "cards": []},
        "/ja/sets": [{"id": "SV8"}], "/ja/sets/SV8": {"id": "SV8", "name": "J", "cards": []},
    }
    sets = tcgdex.fetch(make(routes)[0], LocalStore(tmp_path), now=NOW).sets
    assert set(sets) == {"sv8", "sv8-ja"} and sets["sv8-ja"].language == "ja"


def test_card_404_is_skipped(tmp_path):
    routes = en_routes()
    del routes["/en/cards/sv08-001"]
    assert tcgdex.fetch(make(routes)[0], LocalStore(tmp_path), langs=("en",), now=NOW).cards == {}


def test_other_errors_raise_source_error(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(SourceError):
        tcgdex.fetch(client, LocalStore(tmp_path), langs=("en",), now=NOW)
