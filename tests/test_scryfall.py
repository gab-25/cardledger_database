import gzip
import json
from datetime import date

import httpx
import pytest

from cardledger_database.errors import SourceError
from cardledger_database.http import make_client
from cardledger_database.model import SourceCard, SourceSet
from cardledger_database.sources import scryfall

FILE_URL = "https://data.scryfall.io/default-cards/default-cards-1.jsonl.gz"


def image(name):
    return {"small": f"https://img/{name}-small.jpg", "normal": f"https://img/{name}-normal.jpg"}


CARDS = [
    {"cardmarket_id": 501201, "set": "dmr", "collector_number": "045", "rarity": "uncommon",
     "image_uris": image("counterspell"), "tcgplayer_id": 1001},
    {"set": "dmu", "collector_number": "1", "rarity": "common", "image_uris": image("nocm"),
     "tcgplayer_id": 999},  # no Cardmarket id
    {"cardmarket_id": 600001, "set": "neo", "collector_number": "56", "rarity": "rare",
     "card_faces": [{"image_uris": image("front")}, {"image_uris": image("back")}]},
    {"cardmarket_id": 501301, "set": "dmu", "collector_number": "107", "rarity": "mythic",
     "image_uris": image("sheoldred"), "tcgplayer_id": 2001, "tcgplayer_etched_id": 2002},
    {"cardmarket_id": 501301, "set": "pdmu", "collector_number": "107s", "rarity": "mythic",
     "image_uris": image("promo"), "tcgplayer_id": 2003},  # same Cardmarket id again: first entry wins
]

SETS = [
    {"code": "dmu", "name": "Dominaria United", "released_at": "2022-09-09"},
    {"code": "dmr", "name": "Dominaria Remastered", "released_at": None},
]


class FakeScryfall:
    def __init__(self, cards=CARDS, sets=SETS):
        self.cards, self.sets = cards, sets
        self.fail_file = False
        self.raw_file = None

    def handler(self, request):
        url = str(request.url)
        if url == scryfall.BULK_DATA_URL:
            return httpx.Response(200, json={"data": [
                {"type": "oracle_cards", "jsonl_download_uri": "https://data.scryfall.io/oracle.jsonl.gz"},
                {"type": "default_cards", "jsonl_download_uri": FILE_URL},
            ]})
        if url == FILE_URL:
            if self.fail_file:
                return httpx.Response(500, text="boom")
            body = self.raw_file if self.raw_file is not None else "\n".join(json.dumps(c) for c in self.cards)
            return httpx.Response(200, content=gzip.compress(body.encode()),
                                  headers={"Content-Type": "application/gzip"})
        if url == scryfall.SETS_URL:
            half = len(self.sets) // 2
            return httpx.Response(200, json={"data": self.sets[:half], "has_more": True,
                                             "next_page": scryfall.SETS_URL + "?page=2"})
        if url == scryfall.SETS_URL + "?page=2":
            return httpx.Response(200, json={"data": self.sets[len(self.sets) // 2:], "has_more": False})
        return httpx.Response(404)

    def client(self):
        return make_client(transport=httpx.MockTransport(self.handler))


def test_fetch_streams_and_keeps_first():
    enrichment, _ = scryfall.fetch(FakeScryfall().client())
    cards = enrichment.cards
    assert cards[501201] == SourceCard("dmr", "45", "uncommon", "https://img/counterspell-small.jpg",
                                       "https://img/counterspell-normal.jpg")
    assert set(cards) == {501201, 600001, 501301}  # the card without a Cardmarket id is skipped
    assert cards[600001].image_url == "https://img/front-small.jpg"  # double-faced: front face
    assert cards[501301].set_code == "dmu"  # duplicate Cardmarket id: the first entry wins
    assert enrichment.expansions == {}


def test_fetch_sets_follow_pages():
    enrichment, _ = scryfall.fetch(FakeScryfall().client())
    assert enrichment.sets["dmu"] == SourceSet("dmu", "Dominaria United", date(2022, 9, 9))
    assert enrichment.sets["dmr"] == SourceSet("dmr", "Dominaria Remastered", None)


def test_truth_holds_tcgplayer_ids():
    _, truth = scryfall.fetch(FakeScryfall().client())
    assert truth == {501201: {1001}, 501301: {2001, 2002, 2003}}  # unioned over every entry, no None


def test_long_urls_are_truncated():
    long = {"small": "s" * 600, "normal": "n" * 600}
    card = {"cardmarket_id": 1, "set": "x", "collector_number": "1", "image_uris": long}
    enrichment, _ = scryfall.fetch(FakeScryfall(cards=[card]).client())
    assert len(enrichment.cards[1].image_url) == 500
    assert len(enrichment.cards[1].image_large_url) == 500


def test_http_error_raises():
    fake = FakeScryfall()
    fake.fail_file = True
    with pytest.raises(SourceError):
        scryfall.fetch(fake.client())


def test_malformed_raises():
    fake = FakeScryfall()
    fake.raw_file = '{"cardmarket_id": 1, "set": "x"\nnot json'
    with pytest.raises(SourceError):
        scryfall.fetch(fake.client())
