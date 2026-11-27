import gzip
import json
from datetime import date

import httpx
import pytest

from cardledger_database.model import CardSet, SourceError
from cardledger_database.sources import scryfall

BULK_FILE_URL = "https://data.scryfall.io/all-cards/all-cards-20261126222307.jsonl.gz"
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


def bulk_index(updated_at=UPDATED_AT, kinds=("oracle_cards", "default_cards", "all_cards")):
    return {"object": "list", "has_more": False, "data": [
        {"object": "bulk_data", "type": t, "updated_at": updated_at,
         "jsonl_download_uri": BULK_FILE_URL if t == "all_cards" else f"https://data/{t}.jsonl.gz"}
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
    assert r.image_source == "https://cards.scryfall.io/large/front/a/b/ab12.jpg"
    assert r.source_key == "ab12"
    assert (r.kind, r.name, r.set_code, r.number, r.variant, r.rarity, r.category) == (
        "card", "Black Lotus", "lea", "161", "", "rare", "")
    assert r.image == "" and r.image_large == ""


def test_double_faced_card_uses_front_face_image(tmp_path):
    r = by_id(scryfall.fetch(client(), tmp_path))["mtg:lea:200"]
    assert r.image_source == "https://img/f.jpg"
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


def test_languages_of_a_printing_are_one_row_with_the_english_fields(tmp_path):
    body = jsonl_gz([
        card("lea", "161", "0de", name="Schwarzer Lotus", lang="de",
             image_uris={"large": "https://img/de.jpg"}, rarity="uncommon"),
        card("lea", "161", "zen", name="Black Lotus", lang="en", image_uris={"large": "https://img/en.jpg"}),
        card("lea", "161", "1ja", name="ブラック・ロータス", lang="ja", image_uris={"large": "https://img/ja.jpg"}),
    ])
    rows = scryfall.fetch(client(body=body), tmp_path).rows
    assert len(rows) == 1
    r = rows[0]
    assert r.id == "mtg:lea:161" and r.languages == ("de", "en", "ja")
    assert (r.name, r.rarity, r.image_source, r.source_key) == ("Black Lotus", "rare", "https://img/en.jpg", "zen")


def test_printing_only_in_japanese_takes_its_fields(tmp_path):
    body = jsonl_gz([card("war", "1★", "j1", name="ボーラス", lang="ja", image_uris={"large": "https://img/j.jpg"})])
    r = scryfall.fetch(client(body=body), tmp_path).rows[0]
    assert (r.id, r.languages, r.name, r.image_source, r.source_key) == (
        "mtg:war:1★", ("ja",), "ボーラス", "https://img/j.jpg", "j1")


def test_finishes_are_united_across_languages(tmp_path):
    body = jsonl_gz([card("lea", "161", "a", finishes=["nonfoil"]),
                     card("lea", "161", "b", lang="de", finishes=["foil"]),
                     card("lea", "161", "c", lang="ja", finishes=["etched", "foil"])])
    assert scryfall.fetch(client(body=body), tmp_path).rows[0].finishes == ("etched", "foil", "nonfoil")


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 2, 0)])
def test_representative_without_english_does_not_depend_on_line_order(tmp_path, order):
    objs = [card("lea", "161", "b2", name="Loto B", lang="it"),
            card("lea", "161", "a1", name="Loto A", lang="it"),
            card("lea", "161", "0", name="Lotus noir", lang="fr")]
    body = jsonl_gz([objs[i] for i in order])
    r = scryfall.fetch(client(body=body), tmp_path).rows[0]
    assert (r.name, r.source_key, r.languages) == ("Lotus noir", "0", ("fr", "it"))
    two = jsonl_gz([objs[i] for i in order if i != 2])
    r = scryfall.fetch(client(body=two), tmp_path / "x").rows[0]
    assert (r.name, r.source_key) == ("Loto A", "a1")


def test_excluded_language_object_does_not_join_the_printing(tmp_path):
    body = jsonl_gz([card("lea", "161", "a"), card("lea", "161", "b", lang="de", digital=True)])
    assert scryfall.fetch(client(body=body), tmp_path).rows[0].languages == ("en",)


def test_bulk_lookup_uses_all_cards(tmp_path):
    scryfall.fetch(client(), tmp_path)
    assert BULK_FILE_URL in requested_urls
    assert not any("default_cards" in u for u in requested_urls)
    assert [p.name.split("-")[0] for p in (tmp_path / "scryfall").iterdir()] == ["all_cards"]


def test_old_default_cards_cache_is_removed_after_download(tmp_path):
    folder = tmp_path / "scryfall"
    folder.mkdir()
    (folder / "default_cards-2026-11-20.jsonl.gz").write_bytes(b"old")
    scryfall.fetch(client(), tmp_path)
    assert [p.name for p in folder.iterdir()] == [f"all_cards-{'2026-11-26T22-06-24.338-00-00'}.jsonl.gz"]


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
        scryfall.fetch(client(index=bulk_index(kinds=("oracle_cards", "default_cards"))), tmp_path)


def test_broken_bulk_file_is_source_error(tmp_path):
    with pytest.raises(SourceError):
        scryfall.fetch(client(body=b"not gzip"), tmp_path)


@pytest.mark.parametrize("uris, expected", [
    ({"large": "L", "png": "P", "normal": "N"}, "L"),
    ({"png": "P", "normal": "N"}, "P"),
    ({"normal": "N", "small": "S"}, "N"),
    ({"small": "S"}, ""),
])
def test_image_prefers_large_then_png_then_normal(uris, expected):
    assert scryfall._image({"image_uris": uris}) == expected
    assert scryfall._image({"image_uris": None, "card_faces": [{"image_uris": uris}, {"image_uris": {"large": "B"}}]}) \
        == expected


@pytest.mark.parametrize("lang, expected", [
    ("zhs", "zh-hans"), ("zht", "zh-hant"), ("ph", "x-phyrexian"), ("en", "en"), ("qya", "qya"), ("XX", "xx"),
])
def test_scryfall_lang_is_mapped_to_iso(lang, expected):
    assert scryfall.iso_lang(lang) == expected


def test_row_languages_are_iso_and_english_still_wins(tmp_path):
    body = jsonl_gz([card("lea", "161", "z", name="黑莲花", lang="zhs"), card("lea", "161", "y", lang="zht"),
                     card("lea", "161", "x", lang="ph"), card("lea", "161", "w", name="Black Lotus")])
    r = scryfall.fetch(client(body=body), tmp_path).rows[0]
    assert r.languages == ("en", "x-phyrexian", "zh-hans", "zh-hant") and r.source_key == "w"
