import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cardledger_database.model import SourceError
from cardledger_database.sources import tcgdex

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
    result = tcgdex.fetch(client, tmp_path, langs=("en",), now=NOW)
    c = result.cards[794256]
    assert (c.set_code, c.collector_number, c.rarity) == ("ssp", "1", "Common")
    assert c.image_url == IMG + "/low.webp" and c.image_large_url == IMG + "/high.webp"
    assert result.sets["ssp"].released_at.isoformat() == "2024-11-08"


def test_card_without_image_has_empty_urls(tmp_path):
    routes = en_routes(**{"/en/cards/sv08-001": card("sv08-001", "001", image=None)})
    client, _ = make(routes)
    c = tcgdex.fetch(client, tmp_path, langs=("en",), now=NOW).cards[100]
    assert c.image_url == "" and c.image_large_url == ""


def test_cached_cards_are_not_refetched(tmp_path):
    cache = tmp_path
    tcgdex.fetch(make(en_routes())[0], cache, langs=("en",), now=NOW)
    client, calls = make(en_routes())
    result = tcgdex.fetch(client, cache, langs=("en",), now=NOW + timedelta(days=30))
    assert "/en/cards/sv08-001" not in calls
    assert 794256 in result.cards


def test_cached_card_without_id_is_refetched_after_7_days(tmp_path):
    cache = tmp_path
    no_id = en_routes(**{"/en/cards/sv08-001": card("sv08-001", "001", cm=None)})
    assert tcgdex.fetch(make(no_id)[0], cache, langs=("en",), now=NOW).cards == {}
    client, calls = make(en_routes())
    tcgdex.fetch(client, cache, langs=("en",), now=NOW + timedelta(days=3))
    assert "/en/cards/sv08-001" not in calls
    result = tcgdex.fetch(client, cache, langs=("en",), now=NOW + timedelta(days=8))
    assert "/en/cards/sv08-001" in calls and 794256 in result.cards


def test_set_code_is_official_abbreviation(tmp_path):
    routes = en_routes(**{"/en/sets/sv08": {"id": "sv08", "name": "S", "cards": []}})
    sets = tcgdex.fetch(make(en_routes())[0], tmp_path, langs=("en",), now=NOW).sets
    assert set(sets) == {"ssp"}
    sets = tcgdex.fetch(make(routes)[0], tmp_path / "x", langs=("en",), now=NOW).sets
    assert set(sets) == {"sv08"} and sets["sv08"].released_at is None


def test_colliding_codes_fall_back_to_tcgdex_id(tmp_path):
    routes = {
        "/en/sets": [{"id": "a1"}, {"id": "b1"}],
        "/en/sets/a1": {"id": "a1", "name": "A", "abbreviation": {"official": "XX"}, "cards": []},
        "/en/sets/b1": {"id": "b1", "name": "B", "abbreviation": {"official": "xx"}, "cards": []},
    }
    sets = tcgdex.fetch(make(routes)[0], tmp_path, langs=("en",), now=NOW).sets
    assert set(sets) == {"a1", "b1"}


def test_japanese_sets_are_marked_ja(tmp_path):
    routes = en_routes(**{
        "/ja/sets": [{"id": "SV8"}],
        "/ja/sets/SV8": {"id": "SV8", "name": "超電ブレイカー", "cards": [{"id": "SV8-001", "localId": "001"}]},
        "/ja/cards/SV8-001": card("SV8-001", "001", cm=555),
    })
    result = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW)
    assert result.sets["ssp"].language == "" and result.sets["sv8"].language == "ja"
    assert result.cards[555].set_code == "sv8"


def test_ja_id_colliding_with_en_gets_suffix(tmp_path):
    routes = {
        "/en/sets": [{"id": "sv8"}], "/en/sets/sv8": {"id": "sv8", "name": "E", "cards": []},
        "/ja/sets": [{"id": "SV8"}], "/ja/sets/SV8": {"id": "SV8", "name": "J", "cards": []},
    }
    sets = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW).sets
    assert set(sets) == {"sv8", "sv8-ja"} and sets["sv8-ja"].language == "ja"


def test_card_404_is_skipped(tmp_path):
    routes = en_routes()
    del routes["/en/cards/sv08-001"]
    assert tcgdex.fetch(make(routes)[0], tmp_path, langs=("en",), now=NOW).cards == {}


def test_other_errors_raise_source_error(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(SourceError):
        tcgdex.fetch(client, tmp_path, langs=("en",), now=NOW)


def test_failed_card_keeps_the_cards_fetched_in_the_cache(tmp_path):
    routes = en_routes(**{
        "/en/sets/sv08": {"id": "sv08", "name": "S", "cards": [
            {"id": "sv08-001", "localId": "001"}, {"id": "sv08-002", "localId": "002"},
            {"id": "sv08-003", "localId": "003"}]},
        "/en/cards/sv08-002": card("sv08-002", "002", cm=2),
        "/en/cards/sv08-003": card("sv08-003", "003", cm=3),
    })

    def handler(request):
        path = request.url.path.removeprefix("/v2")
        if path == "/en/cards/sv08-002":
            return httpx.Response(500)
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)

    cache = tmp_path
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SourceError):
        tcgdex.fetch(client, cache, langs=("en",), now=NOW)
    cached = json.loads((cache / "tcgdex" / "en.json").read_text())
    assert set(cached) == {"sv08-001", "sv08-003"}


def test_shared_abbreviation_across_languages_uses_tcgdex_ids(tmp_path):
    routes = {
        "/en/sets": [{"id": "sv08"}],
        "/en/sets/sv08": {"id": "sv08", "name": "E", "abbreviation": {"official": "SSP"}, "cards": []},
        "/ja/sets": [{"id": "SV8"}],
        "/ja/sets/SV8": {"id": "SV8", "name": "J", "abbreviation": {"official": "SSP"}, "cards": []},
    }
    sets = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW).sets
    assert set(sets) == {"sv08", "sv8"}


def test_ids_with_reserved_characters_are_escaped_in_urls(tmp_path):
    def handler(request):
        routes = {
            "/v2/ja/sets": [{"id": "SM1+"}],
            "/v2/ja/sets/SM1%2B": {"id": "SM1+", "name": "J", "cards": [{"id": "SM1+-001", "localId": "001"}]},
            "/v2/ja/cards/SM1%2B-001": card("SM1+-001", "001", cm=777),
        }
        path = request.url.raw_path.decode()
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = tcgdex.fetch(client, tmp_path, langs=("ja",), now=NOW)
    assert result.cards[777].set_code == "sm1+"


def test_cached_card_without_image_is_refetched_after_7_days(tmp_path):
    cache = tmp_path
    no_image = en_routes(**{"/en/cards/sv08-001": card("sv08-001", "001", cm=794256, image=None)})
    assert tcgdex.fetch(make(no_image)[0], cache, langs=("en",), now=NOW).cards[794256].image_url == ""
    client, calls = make(en_routes())
    tcgdex.fetch(client, cache, langs=("en",), now=NOW + timedelta(days=3))
    assert "/en/cards/sv08-001" not in calls
    result = tcgdex.fetch(client, cache, langs=("en",), now=NOW + timedelta(days=8))
    assert "/en/cards/sv08-001" in calls and result.cards[794256].image_url == IMG + "/low.webp"


def test_set_detail_404_skips_the_set(tmp_path, caplog):
    routes = en_routes(**{"/en/sets": [{"id": "gone"}, {"id": "sv08"}]})
    result = tcgdex.fetch(make(routes)[0], tmp_path, langs=("en",), now=NOW)
    assert set(result.sets) == {"ssp"} and 794256 in result.cards
    assert "gone" in caplog.text


def test_set_detail_500_raises_source_error(tmp_path):
    routes = en_routes()

    def handler(request):
        path = request.url.path.removeprefix("/v2")
        if path == "/en/sets/sv08":
            return httpx.Response(500)
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)

    with pytest.raises(SourceError):
        tcgdex.fetch(httpx.Client(transport=httpx.MockTransport(handler)), tmp_path, langs=("en",), now=NOW)


@pytest.mark.parametrize("raw, expected", [("794256", 794256), (794256.0, 794256)])
def test_cardmarket_id_is_coerced_to_int(tmp_path, raw, expected):
    routes = en_routes(**{"/en/cards/sv08-001": card("sv08-001", "001", cm=raw)})
    assert set(tcgdex.fetch(make(routes)[0], tmp_path, langs=("en",), now=NOW).cards) == {expected}


@pytest.mark.parametrize("raw", ["abc", "12.5", True, [1]])
def test_non_integer_cardmarket_id_counts_as_none(tmp_path, raw):
    routes = en_routes(**{"/en/cards/sv08-001": card("sv08-001", "001", cm=raw)})
    cache = tmp_path
    assert tcgdex.fetch(make(routes)[0], cache, langs=("en",), now=NOW).cards == {}
    assert json.loads((cache / "tcgdex" / "en.json").read_text())["sv08-001"]["cm"] is None


def test_transient_errors_are_retried(tmp_path):
    routes = en_routes()
    failed = set()

    def handler(request):
        path = request.url.path.removeprefix("/v2")
        if path not in failed:  # every path fails once
            failed.add(path)
            return httpx.Response(503)
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)

    result = tcgdex.fetch(httpx.Client(transport=httpx.MockTransport(handler)), tmp_path, langs=("en",),
                          now=NOW)
    assert 794256 in result.cards


def test_interrupt_saves_fetched_cards_and_cancels_the_rest(tmp_path, monkeypatch):
    ids = [f"sv08-{n:03}" for n in range(1, 41)]
    routes = en_routes(**{"/en/sets/sv08": {"id": "sv08", "name": "S", "cards": [{"id": i} for i in ids]}})
    routes.update({f"/en/cards/{i}": card(i, i[-3:], cm=n) for n, i in enumerate(ids, 1)})
    client, calls = make(routes)
    read_card = tcgdex._read_card

    def interrupted(client, lang, card_id, set_id, now):
        if card_id == "sv08-003":
            raise KeyboardInterrupt
        return read_card(client, lang, card_id, set_id, now)

    monkeypatch.setattr(tcgdex, "_read_card", interrupted)
    monkeypatch.setattr(tcgdex, "MAX_WORKERS", 1)
    with pytest.raises(KeyboardInterrupt):
        tcgdex.fetch(client, tmp_path, langs=("en",), now=NOW)
    cached = json.loads((tmp_path / "tcgdex" / "en.json").read_text())
    assert set(cached) == {"sv08-001", "sv08-002"}
    assert sum(p.startswith("/en/cards/") for p in calls) < len(ids)


def test_progress_is_saved_every_save_every_cards(tmp_path, monkeypatch, caplog):
    ids = [f"sv08-{n:03}" for n in range(1, 6)]
    routes = en_routes(**{"/en/sets/sv08": {"id": "sv08", "name": "S", "cards": [{"id": i} for i in ids]}})
    routes.update({f"/en/cards/{i}": card(i, i[-3:], cm=n) for n, i in enumerate(ids, 1)})
    client, _ = make(routes)
    monkeypatch.setattr(tcgdex, "SAVE_EVERY", 2)
    with caplog.at_level("INFO"):
        tcgdex.fetch(client, tmp_path, langs=("en",), now=NOW)
    assert "en 2/5 cards fetched" in caplog.text and "en 4/5 cards fetched" in caplog.text


def with_variants(c, *cms):
    c["variants_detailed"] = [{"type": "normal", "thirdParty": {"cardmarket": cm}} for cm in cms]
    return c


def test_variant_cardmarket_ids_get_the_card(tmp_path):
    routes = en_routes(**{"/en/cards/sv08-001": with_variants(card("sv08-001", "001", cm=794256),
                                                               794256, 720365, "794908", "x", None)})
    cards = tcgdex.fetch(make(routes)[0], tmp_path, langs=("en",), now=NOW).cards
    assert set(cards) == {794256, 720365, 794908}
    assert cards[720365] == cards[794256]


def test_variant_id_without_card_level_id(tmp_path):
    routes = en_routes(**{"/en/cards/sv08-001": with_variants(card("sv08-001", "001", cm=None), 720365)})
    assert set(tcgdex.fetch(make(routes)[0], tmp_path, langs=("en",), now=NOW).cards) == {720365}


def test_card_level_ids_win_over_variant_ids(tmp_path):
    routes = en_routes(**{
        "/en/sets/sv08": {
            "id": "sv08", "name": "Surging Sparks", "abbreviation": {"official": "SSP"},
            "cards": [{"id": "sv08-001", "localId": "001"}, {"id": "sv08-002", "localId": "002"}],
        },
        "/en/cards/sv08-001": with_variants(card("sv08-001", "001", cm=1), 2),  # TCGdex error: 2 is card 002
        "/en/cards/sv08-002": card("sv08-002", "002", cm=2),
    })
    cards = tcgdex.fetch(make(routes)[0], tmp_path, langs=("en",), now=NOW).cards
    assert cards[2].collector_number == "2"


def test_cache_entry_without_variant_ids_is_refetched(tmp_path):
    tcgdex._save_cache(tmp_path, "en", {"sv08-001": {
        "cm": 794256, "rarity": "Common", "local_id": "001", "image": IMG, "set": "sv08",
        "fetched_at": NOW.isoformat()}})
    routes = en_routes(**{"/en/cards/sv08-001": with_variants(card("sv08-001", "001", cm=794256), 720365)})
    client, calls = make(routes)
    cards = tcgdex.fetch(client, tmp_path, langs=("en",), now=NOW)
    assert "/en/cards/sv08-001" in calls and 720365 in cards.cards
