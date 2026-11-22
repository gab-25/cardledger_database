import csv
import gzip
import io
from datetime import date

import httpx
import pytest

from cardledger_database.http import make_client
from cardledger_database.model import SourceCard, SourceError, SourceSet
from cardledger_database.sources import mtgjson

SHEOLDRED = "4d04e124-5504-43c9-8bf3-c4c66c291a40"


def small(scryfall_id):
    return f"https://cards.scryfall.io/small/front/{scryfall_id[0]}/{scryfall_id[1]}/{scryfall_id}.jpg"


def normal(scryfall_id):
    return f"https://cards.scryfall.io/normal/front/{scryfall_id[0]}/{scryfall_id[1]}/{scryfall_id}.jpg"


CARDS = [
    {"uuid": "u1", "name": "Llanowar Elves", "setCode": "DMR", "number": "045", "rarity": "uncommon", "side": "", "finishes": "nonfoil, foil"},
    {"uuid": "u2", "name": "Shivan Dragon", "setCode": "DMU", "number": "1", "rarity": "common", "side": "", "finishes": "nonfoil"},  # no Cardmarket id
    {"uuid": "u3a", "name": "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki", "setCode": "NEO", "number": "56", "rarity": "rare", "side": "a", "finishes": "nonfoil"},
    {"uuid": "u3b", "name": "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki", "setCode": "NEO", "number": "56", "rarity": "rare", "side": "b", "finishes": "nonfoil"},  # back face, same id
    {"uuid": "u4", "name": "Sheoldred", "setCode": "DMU", "number": "107★", "rarity": "mythic", "side": "", "finishes": "nonfoil"},
    {"uuid": "u5", "name": "Sheoldred", "setCode": "DMU", "number": "107", "rarity": "mythic", "side": "", "finishes": "nonfoil"},  # same id, plain number
    {"uuid": "u6", "name": "Promo A", "setCode": "PDMU", "number": "200s", "rarity": "rare", "side": "", "finishes": "nonfoil"},
    {"uuid": "u7", "name": "Promo B", "setCode": "PDMU", "number": "201s", "rarity": "rare", "side": "", "finishes": "nonfoil"},  # same id: first wins
    {"uuid": "u8", "name": "Mountain", "setCode": "POR", "number": "208s", "rarity": "common", "side": "", "finishes": "nonfoil", "promoTypes": "schinesealtart"},
    {"uuid": "u9", "name": "Mountain", "setCode": "POR", "number": "209s", "rarity": "common", "side": "", "finishes": "nonfoil"},
    {"uuid": "u10", "name": "Mountain", "setCode": "POR", "number": "210", "rarity": "common", "side": "", "finishes": "nonfoil"},  # same id: really plain number
    {"uuid": "u11", "name": "Alt Art", "setCode": "PTK", "number": "S5a", "rarity": "rare", "side": "", "finishes": "nonfoil"},
    {"uuid": "u12", "name": "Alt Art", "setCode": "PTK", "number": "S5", "rarity": "rare", "side": "", "finishes": "nonfoil", "promoTypes": "boosterfun,schinesealtart"},
    {"uuid": "u13", "name": "Alt Art", "setCode": "PTK", "number": "S6", "rarity": "rare", "side": "", "finishes": "nonfoil"},  # same id: not alt-art, set prefix
    {"uuid": "u14", "name": "Only Alt Art", "setCode": "PTK", "number": "7s", "rarity": "rare", "side": "", "finishes": "nonfoil", "promoTypes": "schinesealtart"},  # kept when alone
]
CARD_IDS = [
    {"uuid": "u1", "mcmId": "501201", "scryfallId": "a1", "tcgplayerProductId": "1001",
     "tcgplayerEtchedProductId": ""},
    {"uuid": "u2", "mcmId": "", "scryfallId": "a2", "tcgplayerProductId": "999", "tcgplayerEtchedProductId": ""},
    {"uuid": "u3a", "mcmId": "600001", "scryfallId": "f3", "tcgplayerProductId": "", "tcgplayerEtchedProductId": ""},
    {"uuid": "u3b", "mcmId": "600001", "scryfallId": "b3", "tcgplayerProductId": "", "tcgplayerEtchedProductId": ""},
    {"uuid": "u4", "mcmId": "501301", "scryfallId": "c4", "tcgplayerProductId": "2001",
     "tcgplayerEtchedProductId": "2002"},
    {"uuid": "u5", "mcmId": "501301", "scryfallId": SHEOLDRED, "tcgplayerProductId": "2003",
     "tcgplayerEtchedProductId": ""},
    {"uuid": "u6", "mcmId": "700001", "scryfallId": "d6", "tcgplayerProductId": "", "tcgplayerEtchedProductId": ""},
    {"uuid": "u7", "mcmId": "700001", "scryfallId": "d7", "tcgplayerProductId": "", "tcgplayerEtchedProductId": ""},
] + [
    {"uuid": uuid, "mcmId": mcm_id, "scryfallId": "", "tcgplayerProductId": "", "tcgplayerEtchedProductId": ""}
    for uuid, mcm_id in [("u8", "700002"), ("u9", "700002"), ("u10", "700002"),
                         ("u11", "700003"), ("u12", "700003"), ("u13", "700003"), ("u14", "700004")]
]
TOKENS = [{"uuid": "t1", "name": "Soldier", "setCode": "TDMU", "number": "3", "side": "", "finishes": "nonfoil"}]  # tokens have no rarity column
TOKEN_IDS = [{"uuid": "t1", "mcmId": "800001", "scryfallId": "e1", "tcgplayerProductId": "3001",
              "tcgplayerEtchedProductId": ""}]
SETS = [
    {"code": "DMU", "name": "Dominaria United", "releaseDate": "2022-09-09", "tokenSetCode": "TDMU"},
    {"code": "DMR", "name": "Dominaria Remastered", "releaseDate": "", "tokenSetCode": ""},
]


def gz_csv(rows):
    out = io.StringIO()
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return gzip.compress(out.getvalue().encode())


class FakeMtgjson:
    def __init__(self):
        self.files = {"cards": CARDS, "cardIdentifiers": CARD_IDS, "tokens": TOKENS, "tokenIdentifiers": TOKEN_IDS,
                      "sets": SETS}
        self.raw: dict[str, bytes] = {}
        self.failing: set[str] = set()

    def handler(self, request):
        url = str(request.url)
        for name, rows in self.files.items():
            if url == mtgjson.CSV_URL.format(name=name):
                if name in self.failing:
                    return httpx.Response(404)
                return httpx.Response(200, content=self.raw.get(name) or gz_csv(rows))
        return httpx.Response(404)

    def client(self):
        return make_client(transport=httpx.MockTransport(self.handler))


def test_cards_keyed_by_cardmarket_id():
    enrichment = mtgjson.fetch(FakeMtgjson().client())
    cards = enrichment.cards
    assert cards[501201] == SourceCard("dmr", "45", "uncommon", small("a1"), normal("a1"))
    assert set(cards) == {501201, 600001, 501301, 700001, 700002, 700003, 700004, 800001}  # the card without a Cardmarket id is skipped
    assert cards[600001].image_url == small("f3")  # double-faced: front face
    assert cards[501301] == SourceCard("dmu", "107", "mythic", small(SHEOLDRED), normal(SHEOLDRED))  # plain number
    assert cards[700001].collector_number == "200S"  # otherwise the first entry wins
    assert enrichment.expansions == {}


def test_several_printings_prefer_plain_number_over_alt_art():
    cards = mtgjson.fetch(FakeMtgjson().client()).cards
    assert (cards[700002].set_code, cards[700002].collector_number) == ("por", "210")  # not 208s (alt art) or 209s
    assert cards[700003].collector_number == "S6"  # S5a has a suffix, S5 is a Chinese alt art
    assert cards[700004].collector_number == "7S"  # an alt art alone is still kept


def test_tokens_have_no_rarity():
    enrichment = mtgjson.fetch(FakeMtgjson().client())
    assert enrichment.cards[800001] == SourceCard("tdmu", "3", "", small("e1"), normal("e1"))


def test_sets():
    enrichment = mtgjson.fetch(FakeMtgjson().client())
    assert enrichment.sets["dmu"] == SourceSet("dmu", "Dominaria United", date(2022, 9, 9))
    assert enrichment.sets["dmr"] == SourceSet("dmr", "Dominaria Remastered", None)
    assert enrichment.sets["tdmu"] == SourceSet("tdmu", "Dominaria United Tokens", date(2022, 9, 9))  # no own row
    assert set(enrichment.sets) == {"dmu", "dmr", "tdmu"}



def test_card_without_scryfall_id_has_no_image():
    fake = FakeMtgjson()
    fake.files["cardIdentifiers"] = [{**row, "scryfallId": ""} for row in CARD_IDS]
    enrichment = mtgjson.fetch(fake.client())
    assert enrichment.cards[501201].image_url == enrichment.cards[501201].image_large_url == ""


def test_http_error_raises():
    fake = FakeMtgjson()
    fake.failing.add("cards")
    with pytest.raises(SourceError):
        mtgjson.fetch(fake.client())


def test_missing_column_raises():
    fake = FakeMtgjson()
    fake.files["cardIdentifiers"] = [{"uuid": "u1", "scryfallId": "a1"}]
    with pytest.raises(SourceError):
        mtgjson.fetch(fake.client())


def test_not_gzip_raises():
    fake = FakeMtgjson()
    fake.raw["sets"] = b"not gzip"
    with pytest.raises(SourceError):
        mtgjson.fetch(fake.client())

