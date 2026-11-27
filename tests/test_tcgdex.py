import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cardledger_database.model import SourceError
from cardledger_database.sources import tcgdex

NOW = datetime(2026, 11, 16, tzinfo=UTC)
OLD = (NOW - timedelta(days=1)).isoformat()
IMG = "https://assets.tcgdex.net/en/sv/sv01/001"
STAMP_LABEL = "set-logo"


def card(cid, local, name="Pineco", image=IMG, variants=None, detailed=None, rarity="Common"):
    c = {"id": cid, "localId": local, "name": name, "set": {"id": cid.rsplit("-", 1)[0]}}
    if rarity:
        c["rarity"] = rarity
    if image:
        c["image"] = image
    c["variants"] = variants if variants is not None else {
        "firstEdition": False, "holo": False, "normal": True, "reverse": False, "wPromo": False}
    if detailed is not None:
        c["variants_detailed"] = detailed
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
        "/en/sets": [{"id": "sv01", "name": "Scarlet & Violet"}],
        "/en/sets/sv01": {
            "id": "sv01", "name": "Scarlet & Violet", "releaseDate": "2023-03-31",
            "serie": {"id": "sv", "name": "Scarlet & Violet"},
            "cards": [{"id": "sv01-001", "localId": "001"}],
        },
        "/en/cards/sv01-001": card(
            "sv01-001", "001",
            variants={"firstEdition": True, "holo": True, "normal": False, "reverse": True, "wPromo": False},
            detailed=[
                {"type": "holo", "size": "standard"},
                {"type": "normal", "size": "standard", "stamp": ["set-logo"]},
            ],
        ),
    }
    routes.update(extra)
    return routes


def full_routes(**extra):
    routes = en_routes(**{
        "/ja/sets": [{"id": "SV1a"}],
        "/ja/sets/SV1a": {"id": "SV1a", "name": "トリプレットビート", "releaseDate": "2023-03-10",
                          "serie": {"id": "SV", "name": "SV"}, "cards": [{"id": "SV1a-001", "localId": "001"}]},
        "/ja/cards/SV1a-001": card("SV1a-001", "001", name="トロピウス",
                                   image="https://assets.tcgdex.net/ja/SV/SV1a/001"),
    })
    routes.update(extra)
    return routes


def by_id(data):
    return {r.id: r for r in data.rows}


def write_cache(cache_dir, lang, cache):
    tcgdex._save_cache(cache_dir, lang, cache)


def test_set_code_is_tcgdex_id_and_rows_are_english(tmp_path):
    data = tcgdex.fetch(make(full_routes())[0], tmp_path, now=NOW)
    sets = {s.code: s for s in data.sets}
    assert set(sets) == {"sv01"} and sets["sv01"].region == ""
    assert sets["sv01"].released_at.isoformat() == "2023-03-31" and sets["sv01"].name == "Scarlet & Violet"
    assert {r.languages for r in data.rows} == {("en",)}


def test_only_english_is_requested(tmp_path):
    routes = full_routes(**{"/fr/sets": [{"id": "sv01"}],
                            "/fr/sets/sv01": {"id": "sv01", "cards": [{"id": "sv01-001"}]}})
    client, calls = make(routes)
    tcgdex.fetch(client, tmp_path, now=NOW)
    assert calls and all(p.startswith("/en/") for p in calls)


def test_existing_english_cache_is_used(tmp_path):
    tcgdex.fetch(make(en_routes())[0], tmp_path, now=NOW)
    write_cache(tmp_path, "ja", {"x": {}})
    client, calls = make(en_routes())
    data = tcgdex.fetch(client, tmp_path, now=NOW + timedelta(days=1))
    assert not [p for p in calls if "/cards/" in p] and "pokemon:sv01:1" in by_id(data)


def test_row_fields(tmp_path):
    r = by_id(tcgdex.fetch(make(en_routes())[0], tmp_path, now=NOW))["pokemon:sv01:1"]
    assert (r.kind, r.name, r.set_code, r.number, r.variant, r.rarity, r.category) == (
        "card", "Pineco", "sv01", "1", "", "Common", "")
    assert r.source_key == "en:sv01-001"


def test_missing_rarity_is_empty(tmp_path):
    routes = en_routes(**{"/en/cards/sv01-001": card("sv01-001", "001", rarity=None)})
    assert by_id(tcgdex.fetch(make(routes)[0], tmp_path, now=NOW))["pokemon:sv01:1"].rarity == ""


def test_pocket_sets_are_excluded(tmp_path):
    routes = en_routes(**{
        "/en/sets": [{"id": "sv01"}, {"id": "A1"}],
        "/en/sets/A1": {"id": "A1", "name": "Genetic Apex", "serie": {"id": "tcgp", "name": "Pokémon TCG Pocket"},
                        "cards": [{"id": "A1-001", "localId": "001"}]},
        "/en/cards/A1-001": card("A1-001", "001", name="Bulbasaur"),
    })
    client, calls = make(routes)
    data = tcgdex.fetch(client, tmp_path, now=NOW)
    assert {s.code for s in data.sets} == {"sv01"}
    assert all(r.set_code != "a1" for r in data.rows)
    assert "/en/cards/A1-001" not in calls


def test_finishes_from_variant_flags(tmp_path):
    data = tcgdex.fetch(make(en_routes())[0], tmp_path, now=NOW)
    assert by_id(data)["pokemon:sv01:1"].finishes == ("first_edition", "holo", "reverse")


def test_unstamped_variant_entry_adds_its_finish(tmp_path):
    routes = en_routes(**{"/en/cards/sv01-001": card("sv01-001", "001", detailed=[
        {"type": "reverse", "size": "standard"}])})
    data = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW)
    assert [r.id for r in data.rows] == ["pokemon:sv01:1"]
    assert data.rows[0].finishes == ("normal", "reverse")


def test_image_source_is_high_webp(tmp_path):
    data = tcgdex.fetch(make(en_routes())[0], tmp_path, now=NOW)
    assert by_id(data)["pokemon:sv01:1"].image_source == "https://assets.tcgdex.net/en/sv/sv01/001/high.webp"


def test_card_without_image_has_empty_image_source(tmp_path):
    routes = en_routes(**{"/en/cards/sv01-001": card("sv01-001", "001", image=None)})
    data = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW)
    assert by_id(data)["pokemon:sv01:1"].image_source == ""


def test_stamped_variant_is_its_own_row_with_the_card_image(tmp_path):
    data = tcgdex.fetch(make(en_routes())[0], tmp_path, now=NOW)
    base = by_id(data)["pokemon:sv01:1"]
    r = by_id(data)["pokemon:sv01:1:" + STAMP_LABEL]
    assert r.variant == STAMP_LABEL and r.image_source == base.image_source
    assert r.finishes == ("normal",)
    assert r.source_key == "en:sv01-001:" + STAMP_LABEL
    assert (r.name, r.set_code, r.number, r.rarity, r.languages) == (
        base.name, base.set_code, base.number, base.rarity, base.languages)


def test_stamp_label_joins_stamp_names_and_falls_back_to_base_finishes(tmp_path):
    routes = en_routes(**{"/en/cards/sv01-001": card("sv01-001", "001", detailed=[
        {"stamp": ["Pokémon Center", "staff"]}])})
    rows = by_id(tcgdex.fetch(make(routes)[0], tmp_path, now=NOW))
    r = rows["pokemon:sv01:1:pokemon-center-staff"]
    assert r.finishes == rows["pokemon:sv01:1"].finishes == ("normal",)


def test_first_edition_stamp_is_a_finish_not_a_row(tmp_path):
    routes = en_routes(**{"/en/cards/sv01-001": card("sv01-001", "001", detailed=[
        {"type": "holo", "subtype": "shadowless", "stamp": ["1st-edition"]}])})
    data = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW)
    assert [r.id for r in data.rows] == ["pokemon:sv01:1"]
    assert data.rows[0].finishes == ("first_edition", "holo", "normal")


def test_stamp_that_slugs_to_nothing_only_adds_its_finish(tmp_path):
    routes = en_routes(**{"/en/cards/sv01-001": card("sv01-001", "001", detailed=[
        {"type": "reverse", "stamp": ["スタッフ"]}])})
    data = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW)
    assert [r.id for r in data.rows] == ["pokemon:sv01:1"]
    assert data.rows[0].finishes == ("normal", "reverse")


def test_same_stamp_on_two_finishes_is_one_row(tmp_path):
    routes = en_routes(**{"/en/cards/sv01-001": card("sv01-001", "001", detailed=[
        {"type": "normal", "stamp": ["set-logo"]}, {"type": "reverse", "stamp": ["set-logo"]}])})
    data = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW)
    assert sorted(r.id for r in data.rows) == ["pokemon:sv01:1", "pokemon:sv01:1:set-logo"]
    assert by_id(data)["pokemon:sv01:1:set-logo"].finishes == ("normal", "reverse")


def test_cache_entry_fields(tmp_path):
    tcgdex.fetch(make(en_routes())[0], tmp_path, now=NOW)
    entry = json.loads((tmp_path / "tcgdex" / "en.json").read_text())["sv01-001"]
    assert entry["rarity"] == "Common" and entry["local_id"] == "001" and entry["image"] == IMG
    assert entry["name"] == "Pineco" and entry["set"] == "sv01" and entry["fetched_at"] == NOW.isoformat()
    assert entry["finishes"] == ["first_edition", "holo", "reverse"] and entry["stamps"] == ["set-logo"]
    assert "cm" not in entry and "variant_cms" not in entry


def test_v1_cache_entries_are_refetched(tmp_path):
    write_cache(tmp_path, "en", {"sv01-001": {"cm": 5, "variant_cms": [], "image": "i", "fetched_at": OLD}})
    client, requested_urls = make(en_routes())
    data = tcgdex.fetch(client, tmp_path, now=NOW)
    assert "/en/cards/sv01-001" in requested_urls
    assert by_id(data)["pokemon:sv01:1"].image_source == IMG + "/high.webp"


def test_cached_cards_are_not_refetched(tmp_path):
    tcgdex.fetch(make(en_routes())[0], tmp_path, now=NOW)
    client, calls = make(en_routes())
    data = tcgdex.fetch(client, tmp_path, now=NOW + timedelta(days=30))
    assert "/en/cards/sv01-001" not in calls
    assert set(by_id(data)) == {"pokemon:sv01:1", "pokemon:sv01:1:set-logo"}
    assert by_id(data)["pokemon:sv01:1:set-logo"].finishes == ("normal",)


def test_cached_card_without_image_is_refetched_after_7_days(tmp_path):
    no_image = en_routes(**{"/en/cards/sv01-001": card("sv01-001", "001", image=None)})
    tcgdex.fetch(make(no_image)[0], tmp_path, now=NOW)
    client, calls = make(en_routes())
    tcgdex.fetch(client, tmp_path, now=NOW + timedelta(days=3))
    assert "/en/cards/sv01-001" not in calls
    data = tcgdex.fetch(client, tmp_path, now=NOW + timedelta(days=8))
    assert "/en/cards/sv01-001" in calls
    assert by_id(data)["pokemon:sv01:1"].image_source == IMG + "/high.webp"


def test_set_without_release_date(tmp_path):
    routes = en_routes(**{"/en/sets/sv01": {"id": "sv01", "name": "S", "cards": []}})
    data = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW)
    assert [(s.code, s.released_at) for s in data.sets] == [("sv01", None)]


def test_card_404_is_skipped(tmp_path):
    routes = en_routes()
    del routes["/en/cards/sv01-001"]
    assert tcgdex.fetch(make(routes)[0], tmp_path, now=NOW).rows == []


def test_other_errors_raise_source_error(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(SourceError):
        tcgdex.fetch(client, tmp_path, now=NOW)


def test_failed_card_keeps_the_cards_fetched_in_the_cache(tmp_path):
    routes = en_routes(**{
        "/en/sets/sv01": {"id": "sv01", "name": "S", "cards": [
            {"id": "sv01-001", "localId": "001"}, {"id": "sv01-002", "localId": "002"},
            {"id": "sv01-003", "localId": "003"}]},
        "/en/cards/sv01-002": card("sv01-002", "002"),
        "/en/cards/sv01-003": card("sv01-003", "003"),
    })

    def handler(request):
        path = request.url.path.removeprefix("/v2")
        if path == "/en/cards/sv01-002":
            return httpx.Response(500)
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SourceError):
        tcgdex.fetch(client, tmp_path, now=NOW)
    cached = json.loads((tmp_path / "tcgdex" / "en.json").read_text())
    assert set(cached) == {"sv01-001", "sv01-003"}


def test_ids_with_reserved_characters_are_escaped_in_urls(tmp_path):
    def handler(request):
        routes = {
            "/v2/en/sets": [{"id": "SM1+"}],
            "/v2/en/sets/SM1%2B": {"id": "SM1+", "name": "J", "cards": [{"id": "SM1+-001", "localId": "001"}]},
            "/v2/en/cards/SM1%2B-001": card("SM1+-001", "001"),
        }
        path = request.url.raw_path.decode()
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    data = tcgdex.fetch(client, tmp_path, now=NOW)
    assert [r.id for r in data.rows] == ["pokemon:sm1+:1"]


def test_set_detail_404_skips_the_set(tmp_path, caplog):
    routes = en_routes(**{"/en/sets": [{"id": "gone"}, {"id": "sv01"}]})
    data = tcgdex.fetch(make(routes)[0], tmp_path, now=NOW)
    assert {s.code for s in data.sets} == {"sv01"} and "pokemon:sv01:1" in by_id(data)
    assert "gone" in caplog.text


def test_set_detail_500_raises_source_error(tmp_path):
    routes = en_routes()

    def handler(request):
        path = request.url.path.removeprefix("/v2")
        if path == "/en/sets/sv01":
            return httpx.Response(500)
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)

    with pytest.raises(SourceError):
        tcgdex.fetch(httpx.Client(transport=httpx.MockTransport(handler)), tmp_path, now=NOW)


def test_transient_errors_are_retried(tmp_path):
    routes = en_routes()
    failed = set()

    def handler(request):
        path = request.url.path.removeprefix("/v2")
        if path not in failed:  # every path fails once
            failed.add(path)
            return httpx.Response(503)
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)

    data = tcgdex.fetch(httpx.Client(transport=httpx.MockTransport(handler)), tmp_path, now=NOW)
    assert "pokemon:sv01:1" in by_id(data)


def test_interrupt_saves_fetched_cards_and_cancels_the_rest(tmp_path, monkeypatch):
    ids = [f"sv01-{n:03}" for n in range(1, 41)]
    routes = en_routes(**{"/en/sets/sv01": {"id": "sv01", "name": "S", "cards": [{"id": i} for i in ids]}})
    routes.update({f"/en/cards/{i}": card(i, i[-3:]) for i in ids})
    client, calls = make(routes)
    read_card = tcgdex._read_card

    def interrupted(client, lang, card_id, set_id, now):
        if card_id == "sv01-003":
            raise KeyboardInterrupt
        return read_card(client, lang, card_id, set_id, now)

    monkeypatch.setattr(tcgdex, "_read_card", interrupted)
    monkeypatch.setattr(tcgdex, "MAX_WORKERS", 1)
    with pytest.raises(KeyboardInterrupt):
        tcgdex.fetch(client, tmp_path, now=NOW)
    cached = json.loads((tmp_path / "tcgdex" / "en.json").read_text())
    assert set(cached) == {"sv01-001", "sv01-002"}
    assert sum(p.startswith("/en/cards/") for p in calls) < len(ids)


def test_progress_is_saved_every_save_every_cards(tmp_path, monkeypatch, caplog):
    ids = [f"sv01-{n:03}" for n in range(1, 6)]
    routes = en_routes(**{"/en/sets/sv01": {"id": "sv01", "name": "S", "cards": [{"id": i} for i in ids]}})
    routes.update({f"/en/cards/{i}": card(i, i[-3:]) for i in ids})
    client, _ = make(routes)
    monkeypatch.setattr(tcgdex, "SAVE_EVERY", 2)
    with caplog.at_level("INFO"):
        tcgdex.fetch(client, tmp_path, now=NOW)
    assert "en 2/5 cards fetched" in caplog.text and "en 4/5 cards fetched" in caplog.text
