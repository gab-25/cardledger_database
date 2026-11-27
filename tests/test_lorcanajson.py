import httpx
import pytest

from cardledger_database.model import SourceError
from cardledger_database.sources import lorcanajson


def card(id, set_code, number, rarity="Common", foil=("None", "Silver"), name="Ariel - On Human Legs"):
    return {"id": id, "setCode": set_code, "number": number, "rarity": rarity, "foilTypes": list(foil),
            "fullName": name, "images": {"full": f"https://img/full{id}.avif", "thumbnail": f"https://img/t{id}.avif"},
            "externalLinks": {"cardmarketId": 999}}


SETS = {
    "1": {"name": "The First Chapter", "releaseDate": "2023-09-01", "number": 1},
    "Q1": {"name": "Illumineer's Quest", "number": 101},
}
EN = {"sets": SETS, "cards": [
    card(5, "1", 5, "Uncommon", ("Silver",), "Elsa - Snow Queen"),
    card(6, "1", 6, "Rare"),
    card(20, "Q1", 2, "Promo"),
]}
FR = {"sets": SETS, "cards": [card(5, "1", 5)]}
EMPTY = {"sets": {}, "cards": []}


def client(payloads, status=200):
    def handler(request):
        for lang, payload in payloads.items():
            if str(request.url) == lorcanajson.URL.format(lang=lang):
                return httpx.Response(status, json=payload)
        raise AssertionError(request.url)

    return httpx.Client(transport=httpx.MockTransport(handler))


def by_id(data):
    return {r.id: r for r in data.rows}


ALL = {"en": EN, "fr": FR, "de": EN, "it": EMPTY}


def test_rows_from_english_with_languages_from_all_files():
    data = lorcanajson.fetch(client(ALL))
    r = by_id(data)["lorcana:1:5"]
    assert r.languages == ("de", "en", "fr") and r.number == "5" and r.rarity == "Uncommon"
    assert r.finishes == ("silver",)
    assert r.image_source == "https://img/full5.avif" and r.image == "" and r.image_large == ""
    assert r.kind == "card" and r.name == "Elsa - Snow Queen" and r.set_code == "1"
    assert r.variant == "" and r.category == "" and r.source_key == "5"
    assert by_id(data)["lorcana:1:6"].languages == ("de", "en")
    assert by_id(data)["lorcana:1:6"].finishes == ("non_foil", "silver")


def test_no_market_ids_and_promo_set_is_its_own_set():
    data = lorcanajson.fetch(client(ALL))
    assert "lorcana:q1:2" in by_id(data) and by_id(data)["lorcana:q1:2"].set_code == "q1"
    assert {s.code for s in data.sets} >= {"1", "q1"}
    first = next(s for s in data.sets if s.code == "1")
    assert first.name == "The First Chapter" and str(first.released_at) == "2023-09-01" and first.region == ""
    assert not any("999" in str(r) for r in data.rows)


@pytest.mark.parametrize("bad", [{"sets": {}}, []])
def test_bad_shape_is_source_error(bad):
    with pytest.raises(SourceError):
        lorcanajson.fetch(client({**ALL, "fr": bad}))
    with pytest.raises(SourceError):
        lorcanajson.fetch(client({**ALL, "en": bad}))


def test_http_error_is_source_error():
    with pytest.raises(SourceError):
        lorcanajson.fetch(client(ALL, status=500))


def test_english_card_missing_fields_is_source_error():
    with pytest.raises(SourceError):
        lorcanajson.fetch(client({**ALL, "en": {"sets": {}, "cards": [{"id": 1}]}}))
