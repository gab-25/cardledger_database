from datetime import date

import httpx
import pytest

from cardledger_database.errors import SourceError
from cardledger_database.model import SourceCard, SourceSet
from cardledger_database.sources import lorcanajson

PAYLOAD = {
    "metadata": {"formatVersion": "2.0.0"},
    "sets": {
        "1": {"name": "The First Chapter", "releaseDate": "2023-09-01", "number": 1},
        "Q1": {"name": "Illumineer's Quest", "number": 101},
    },
    "cards": [
        {"setCode": "1", "number": 5, "rarity": "Uncommon", "externalLinks": {"cardmarketId": 7001},
         "images": {"full": "https://img/full5.avif", "thumbnail": "https://img/thumb5.avif", "foilMask": "x"}},
        {"setCode": "1", "number": 6, "rarity": "Rare", "externalLinks": {},
         "images": {"full": "https://img/full6.avif", "thumbnail": "https://img/thumb6.avif"}},
        {"setCode": "Q1", "number": 2, "rarity": "Promo", "images": {"full": "f", "thumbnail": "t"}},
        {"setCode": "Q1", "number": 3, "rarity": "Promo", "externalLinks": {"cardmarketId": 7001},
         "images": {"full": "f3", "thumbnail": "t3"}},  # duplicate id: first wins
    ],
}


def client(payload=PAYLOAD, status=200, content=None):
    def handler(request):
        assert str(request.url) == lorcanajson.URL
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_card_with_cardmarket_id():
    cards = lorcanajson.fetch(client()).cards
    assert cards == {7001: SourceCard("1", "5", "Uncommon", "https://img/thumb5.avif", "https://img/full5.avif")}


def test_card_without_cardmarket_id_is_ignored():
    assert set(lorcanajson.fetch(client()).cards) == {7001}


def test_sets_from_metadata():
    sets = lorcanajson.fetch(client()).sets
    assert sets == {
        "1": SourceSet("1", "The First Chapter", date(2023, 9, 1)),
        "q1": SourceSet("q1", "Illumineer's Quest", None),
    }


@pytest.mark.parametrize("kwargs", [
    {"status": 500},
    {"content": b"not json"},
    {"payload": {"sets": {}}},
    {"payload": []},
])
def test_bad_payload_raises_source_error(kwargs):
    with pytest.raises(SourceError):
        lorcanajson.fetch(client(**kwargs))
