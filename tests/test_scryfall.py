import gzip
import json
from datetime import date

import httpx
import pytest

from cardledger_database.model import CardSet, SourceError
from cardledger_database.sources import scryfall

BULK_FILE_URL = "https://data.scryfall.io/default-cards/default-cards-20261126220624.jsonl.gz"
UPDATED_AT = "2026-11-26T22:06:24.338+00:00"


def card(set_code, number, id, name="Black Lotus", lang="en", **extra):
    c = {"object": "card", "id": id, "name": name, "lang": lang, "set": set_code, "collector_number": number,
         "rarity": "rare", "finishes": ["nonfoil", "foil"], "digital": False, "games": ["paper", "mtgo"],
         "layout": "normal",
         "image_uris": {"large": f"https://cards.scryfall.io/large/front/a/b/{id}.jpg",
                        "png": f"https://cards.scryfall.io/png/front/a/b/{id}.png"}}
    c.update(extra)
    return c


CARDS = [
    card("lea", "161", "ab12"),
    card("lea", "200", "dfc1", name="Delver of Secrets // Insectile Aberration", layout="transform",
         image_uris=None, card_faces=[
             {"name": "Delver of Secrets", "image_uris": {"png": "https://img/front.png", "large": "https://img/f.jpg"}},
             {"name": "Insectile Aberration", "image_uris": {"png": "https://img/back.png"}}]),
    card("tlea", "1", "tok1", name="Soldier", layout="token", rarity="common"),
    card("lea", "300", "dig1", digital=True),
    card("lea", "301", "arena1", games=["arena"]),
    card("ylea", "1", "alch1"),
    card("alea", "1", "art1", layout="art_series"),
    card("flea", "1", "front1", layout="front_card"),
    card("jpn", "5", "ja1", name="Forest", lang="ja", finishes=["etched"]),
    card("sld", "123★", "star1"),
    card("lea", "400", "noimg", image_uris=None),
]
for c in CARDS:
    if c.get("image_uris") is None:
        c.pop("image_uris")

SETS = {"object": "list", "has_more": False, "data": [
    {"code": "lea", "name": "Limited Edition Alpha", "released_at": "1993-08-05", "digital": False},
    {"code": "tlea", "name": "Alpha Tokens", "released_at": "1993-08-05", "digital": False},
    {"code": "ylea", "name": "Alchemy: Alpha", "released_at": "2022-01-01", "digital": True},
    {"code": "sld", "name": "Secret Lair Drop", "digital": False},
]}


def bulk_index(updated_at=UPDATED_AT, kinds=("oracle_cards", "default_cards")):
    return {"object": "list", "has_more": False, "data": [
        {"object": "bulk_data", "type": t, "updated_at": updated_at,
         "jsonl_download_uri": BULK_FILE_URL if t == "default_cards" else f"https://data/{t}.jsonl.gz"}
        for t in kinds]}


def jsonl_gz(cards):
    return gzip.compress("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards).encode())


requested: list[httpx.Request] = []
requested_urls: list[str] = []


def client(index=None, body=None, file_status=200):
    index = bulk_index() if index is None else index
    body = jsonl_gz(CARDS) if body is None else body

    def handler(request):
        requested.append(request)
        requested_urls.append(str(request.url))
        url = str(request.url)
        if url == scryfall.BULK_URL:
            return httpx.Response(200, json=index)
        if url == scryfall.SETS_URL:
            return httpx.Response(200, json=SETS)
        if url == BULK_FILE_URL:
            return httpx.Response(file_status, content=body, headers={"Content-Type": "application/gzip"})
        raise AssertionError(url)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def reset_requests():
    requested.clear()
    requested_urls.clear()


def by_id(data):
    return {r.id: r for r in data.rows}


def test_card_row(tmp_path):
    r = by_id(scryfall.fetch(client(), tmp_path))["mtg:lea:161"]
    assert r.finishes == ("foil", "nonfoil") and r.languages == ("en",)
    assert r.image_source == "https://cards.scryfall.io/png/front/a/b/ab12.png"
    assert r.source_key == "ab12"
    assert (r.kind, r.name, r.set_code, r.number, r.variant, r.rarity, r.category) == (
        "card", "Black Lotus", "lea", "161", "", "rare", "")
    assert r.image == "" and r.image_large == ""


def test_double_faced_card_uses_front_face_image(tmp_path):
    r = by_id(scryfall.fetch(client(), tmp_path))["mtg:lea:200"]
    assert r.image_source == "https://img/front.png"
    assert r.name == "Delver of Secrets // Insectile Aberration"


def test_card_without_any_image_keeps_an_empty_source(tmp_path):
    assert by_id(scryfall.fetch(client(), tmp_path))["mtg:lea:400"].image_source == ""


def test_token_is_in_its_own_set(tmp_path):
    data = scryfall.fetch(client(), tmp_path)
    r = by_id(data)["mtg:tlea:1"]
    assert r.set_code == "tlea" and r.name == "Soldier" and r.rarity == "common"
    assert CardSet("tlea", "Alpha Tokens", date(1993, 8, 5)) in data.sets


def test_digital_cards_sets_and_art_series_are_excluded(tmp_path):
    data = scryfall.fetch(client(), tmp_path)
    keys = {r.source_key for r in data.rows}
    assert keys == {"ab12", "dfc1", "tok1", "ja1", "star1", "noimg"}
    assert "ylea" not in {s.code for s in data.sets}


def test_sets_without_release_date(tmp_path):
    sets = {s.code: s for s in scryfall.fetch(client(), tmp_path).sets}
    assert sets["sld"] == CardSet("sld", "Secret Lair Drop", None)
    assert sets["lea"] == CardSet("lea", "Limited Edition Alpha", date(1993, 8, 5))


def test_non_english_only_card_keeps_its_language(tmp_path):
    r = by_id(scryfall.fetch(client(), tmp_path))["mtg:jpn:5"]
    assert r.languages == ("ja",) and r.finishes == ("etched",)


def test_collector_number_with_symbol_is_kept(tmp_path):
    r = by_id(scryfall.fetch(client(), tmp_path))["mtg:sld:123★"]
    assert r.number == "123★"


def test_same_printing_twice_is_left_to_assemble(tmp_path):
    body = jsonl_gz([card("lea", "161", "a"), card("lea", "161", "b", lang="de")])
    rows = scryfall.fetch(client(body=body), tmp_path).rows
    assert [(r.id, r.source_key) for r in rows] == [("mtg:lea:161", "a"), ("mtg:lea:161", "b")]


def test_api_requests_ask_for_json(tmp_path):
    scryfall.fetch(client(), tmp_path)
    api = [r for r in requested if str(r.url) in (scryfall.BULK_URL, scryfall.SETS_URL)]
    assert len(api) == 2 and all(r.headers["Accept"] == "application/json" for r in api)


def test_cached_bulk_is_not_downloaded_again(tmp_path):
    scryfall.fetch(client(), tmp_path)
    scryfall.fetch(client(), tmp_path)
    assert requested_urls.count(BULK_FILE_URL) == 1


def test_new_bulk_replaces_the_old_cached_copy(tmp_path):
    scryfall.fetch(client(), tmp_path)
    scryfall.fetch(client(index=bulk_index("2026-11-27T22:00:00.000+00:00")), tmp_path)
    assert requested_urls.count(BULK_FILE_URL) == 2
    assert len(list((tmp_path / "scryfall").iterdir())) == 1


def test_failed_download_keeps_the_previous_cache(tmp_path):
    scryfall.fetch(client(), tmp_path)
    before = sorted(p.name for p in (tmp_path / "scryfall").iterdir())
    with pytest.raises(SourceError):
        scryfall.fetch(client(index=bulk_index("2026-11-27T22:00:00.000+00:00"), file_status=404), tmp_path)
    assert sorted(p.name for p in (tmp_path / "scryfall").iterdir()) == before


def test_bad_shape_is_source_error(tmp_path):
    with pytest.raises(SourceError):
        scryfall.fetch(client(index=bulk_index(kinds=("oracle_cards",))), tmp_path)


def test_broken_bulk_file_is_source_error(tmp_path):
    with pytest.raises(SourceError):
        scryfall.fetch(client(body=b"not gzip"), tmp_path)
