import json
import logging
import time
from datetime import date

import httpx
import pytest

from cardledger_database.model import CardSet, SourceError
from cardledger_database.sources import ygoprodeck


def card(id, name, sets, images=1):
    return {"id": id, "name": name, "type": "Normal Monster",
            "card_sets": [{"set_name": "x", "set_code": code, "set_rarity": rarity, "set_rarity_code": "(UR)",
                           "set_price": "0"} for code, rarity in sets],
            "card_images": [{"id": id + i, "image_url": f"https://images.ygoprodeck.com/images/cards/{id + i}.jpg",
                             "image_url_small": "s", "image_url_cropped": "c"} for i in range(images)]}


CARDS = {"data": [
    card(89631139, "Blue-Eyes White Dragon",
         [("LOB-EN001", "Ultra Rare"), ("LOB-EN001", "Secret Rare"),
          ("SDK-E001", "Common")], images=4),
    card(46986414, "Dark Magician", [("LOB-005", "Ultra Rare"), ("DB2-EN001", "Super Rare")]),
    {"id": 1, "name": "Token", "type": "Token", "card_images": []},  # no card_sets: no row
]}
PROMO_CARDS = {"data": [card(10, "Promo One", [("PROMO", "Rare")]), card(20, "Promo Two", [("PROMO-X", "Rare")])]}
SETS = [
    {"set_name": "Legend of Blue Eyes White Dragon", "set_code": "LOB", "num_of_cards": 126, "tcg_date": "2002-03-08"},
    {"set_name": "Starter Deck: Kaiba", "set_code": "SDK", "num_of_cards": 50, "tcg_date": None},
    {"set_name": "Unused Set", "set_code": "UNU", "num_of_cards": 1, "tcg_date": "2010-01-01"},
]


def client(cards=CARDS, sets=SETS, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if str(request.url) == ygoprodeck.CARDS_URL:
            return httpx.Response(200, json=cards)
        if str(request.url) == ygoprodeck.SETS_URL:
            return httpx.Response(200, json=sets)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def failing_client(calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(503)
    return httpx.Client(transport=httpx.MockTransport(handler))


def by_id(data):
    return {r.id: r for r in data.rows}


def test_each_set_number_and_rarity_is_a_row(tmp_path):
    rows = by_id(ygoprodeck.fetch(client(), tmp_path))
    assert rows["yugioh:lob:LOB-001:ultra-rare"].number == "LOB-EN001"
    assert "yugioh:lob:LOB-001:secret-rare" in rows             # same number, second rarity
    r = rows["yugioh:lob:LOB-001:secret-rare"]
    assert (r.kind, r.name, r.set_code, r.variant, r.rarity, r.languages, r.finishes, r.category) == (
        "card", "Blue-Eyes White Dragon", "lob", "secret-rare", "Secret Rare", ("en",), (), "")
    assert r.source_key == "89631139:LOB-EN001:Secret Rare"
    assert rows["yugioh:sdk:SDK-001:common"].number == "SDK-E001"
    assert rows["yugioh:db2:DB2-001:super-rare"].set_code == "db2"


def test_region_letters_out_of_the_id_merge_old_and_new_numbers(tmp_path):
    cards = {"data": [card(89631139, "Blue-Eyes White Dragon", [("LOB-EN001", "Ultra Rare"), ("LOB-001", "Ultra Rare")])]}
    data = ygoprodeck.fetch(client(cards), tmp_path)
    same = [r for r in data.rows if r.id == "yugioh:lob:LOB-001:ultra-rare"]
    assert sorted(r.number for r in same) == ["LOB-001", "LOB-EN001"]  # assemble merges them by id


def test_set_name_and_date_from_cardsets(tmp_path):
    data = ygoprodeck.fetch(client(), tmp_path)
    assert CardSet("lob", "Legend of Blue Eyes White Dragon", date(2002, 3, 8)) in data.sets
    assert CardSet("sdk", "Starter Deck: Kaiba", None) in data.sets
    codes = {s.code for s in data.sets}
    assert "db2" not in codes  # no cardsets entry: assemble invents it
    assert "unu" not in codes  # no printing in it


def test_set_name_is_alphabetically_first_when_several(tmp_path, caplog):
    sets = SETS + [
        {"set_name": "Absolute LOB Sneak Peek", "set_code": "lob", "num_of_cards": 1, "tcg_date": "2002-03-09"},
        {"set_name": "LOB: Special Edition", "set_code": "LOB", "num_of_cards": 1, "tcg_date": "2002-01-01"},
        {"set_name": "Starter Deck: Kaiba Reloaded", "set_code": "SDK", "num_of_cards": 1, "tcg_date": None},
    ]
    with caplog.at_level(logging.WARNING):
        data = ygoprodeck.fetch(client(sets=sets), tmp_path)
    assert CardSet("lob", "Absolute LOB Sneak Peek", date(2002, 1, 1)) in data.sets
    assert CardSet("sdk", "Starter Deck: Kaiba", None) in data.sets
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "2 set prefixes" in warnings[0]


def test_unparsable_number_is_not_merged(tmp_path):
    rows = by_id(ygoprodeck.fetch(client(PROMO_CARDS), tmp_path))   # "PROMO" and "PROMO-X" with the same rarity
    assert len([r for r in rows.values() if r.set_code == "promo"]) == 2
    assert rows["yugioh:promo:PROMO:rare"].number == "PROMO"
    assert rows["yugioh:promo:PROMO-X:rare"].number == "PROMO-X"


def test_image_only_for_single_artwork(tmp_path):
    rows = by_id(ygoprodeck.fetch(client(), tmp_path))
    assert rows["yugioh:lob:LOB-005:ultra-rare"].image_source == "https://images.ygoprodeck.com/images/cards/46986414.jpg"
    assert rows["yugioh:lob:LOB-001:secret-rare"].image_source == ""  # 4 artworks


def test_cached_responses_are_reused_for_a_day(tmp_path):
    calls = []
    first = ygoprodeck.fetch(client(calls=calls), tmp_path)
    assert sorted(calls) == sorted([ygoprodeck.CARDS_URL, ygoprodeck.SETS_URL])
    assert (tmp_path / "ygoprodeck" / "cardinfo.json").exists() and (tmp_path / "ygoprodeck" / "cardsets.json").exists()
    calls.clear()
    second = ygoprodeck.fetch(client(calls=calls), tmp_path)
    assert calls == []
    assert second == first


def test_cache_older_than_a_day_is_downloaded_again(tmp_path):
    ygoprodeck.fetch(client(), tmp_path)
    path = tmp_path / "ygoprodeck" / "cardinfo.json"
    cached = json.loads(path.read_text())
    cached["downloaded_at"] = time.time() - 25 * 3600
    path.write_text(json.dumps(cached))
    calls = []
    data = ygoprodeck.fetch(client(PROMO_CARDS, calls=calls), tmp_path)
    assert calls == [ygoprodeck.CARDS_URL]
    assert {r.set_code for r in data.rows} == {"promo"}


def test_failed_download_falls_back_to_the_cache(tmp_path, caplog):
    first = ygoprodeck.fetch(client(), tmp_path)
    for name in ("cardinfo.json", "cardsets.json"):
        path = tmp_path / "ygoprodeck" / name
        cached = json.loads(path.read_text())
        cached["downloaded_at"] = 0
        path.write_text(json.dumps(cached))
    with caplog.at_level(logging.WARNING):
        data = ygoprodeck.fetch(failing_client(), tmp_path)
    assert data == first
    assert any("cached" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_failed_download_without_cache_is_source_error(tmp_path):
    with pytest.raises(SourceError):
        ygoprodeck.fetch(failing_client(), tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_bad_shape_is_source_error(tmp_path):
    with pytest.raises(SourceError):
        ygoprodeck.fetch(client(cards={"error": "nope"}), tmp_path)
    assert not (tmp_path / "ygoprodeck" / "cardinfo.json").exists()  # a bad answer is not cached
