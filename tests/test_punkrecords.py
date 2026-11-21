import httpx
import pytest

from cardledger_database.model import CmProduct, SourceCard, SourceError, SourceSet
from cardledger_database.sources import punkrecords

PACKS = {
    "569105": {"id": "569105", "raw_title": "BOOSTER PACK -AWAKENING OF THE NEW ERA- [OP-05]",
               "title_parts": {"label": "OP-05", "prefix": "BOOSTER PACK", "title": "Awakening of the New Era"}},
    "569202": {"id": "569202", "raw_title": "EXTRA BOOSTER -Anime 25th Collection- [EB-02]",
               "title_parts": {"label": "EB-02", "prefix": "EXTRA BOOSTER", "title": "Anime 25th Collection"}},
}


def card(code, pack, rarity):
    return {"card_id": code.split("_")[0], "pack_id": pack, "rarity": rarity,
            "img_url": f"https://en.onepiece-cardgame.com/images/{code}.png?1"}


CARDS = {
    "OP05-001": card("OP05-001", "569105", "Common"),
    "OP05-098": card("OP05-098", "569105", "Leader"),
    "OP05-098_p1": card("OP05-098_p1", "569105", "Leader"),
    "OP05-098_p2": card("OP05-098_p2", "569202", "Leader"),
    "OP05-060": card("OP05-060", "569105", "Rare"),
    "OP05-060_p1": card("OP05-060_p1", "569202", "SuperRare"),
}


def prod(id_product, name, id_expansion, id_category=1):
    return CmProduct(id_product, name, id_category, "One Piece Single", id_expansion, None)


PRODUCTS = [
    prod(1, "A (OP05-001)", 10),
    prod(2, "Enel (OP05-098)", 10),
    prod(3, "Enel (OP05-098)", 10),
    prod(4, "B (OP05-060)", 10),
    prod(5, "Promo (P-001)", 10),
    prod(6, "DON!!", 10),
    prod(7, "Enel (OP05-098)", 20),
    prod(9, "B (OP05-060)", 20),
    prod(8, "Box (OP05-001)", 10, id_category=2),
]
SINGLES = {1}


def build(cards=CARDS, packs=PACKS, products=PRODUCTS):
    return punkrecords.build(packs, cards, products, SINGLES)


def test_set_code():
    assert punkrecords.set_code("569105", "OP-05") == "op05"
    assert punkrecords.set_code("x", "PRB-01") == "prb01"
    assert punkrecords.set_code("x", "OP14-EB04") == "op14-eb04"
    assert punkrecords.set_code("569901", None) == "pk569901"
    assert punkrecords.set_code("569901", "") == "pk569901"


def test_expansion_vote():
    assert build().expansions == {10: "op05"}


def test_unique_variant_gets_image():
    e = build()
    url = CARDS["OP05-001"]["img_url"]
    assert e.cards[1] == SourceCard("op05", "OP05-001", "C", url, url)
    assert e.cards[4].rarity == "R" and e.cards[4].image_url == CARDS["OP05-060"]["img_url"]


def test_same_code_twice_in_expansion_has_no_image():
    e = build()
    assert e.cards[2] == e.cards[3] == SourceCard("op05", "OP05-098", "L", "", "")


def test_unmapped_expansion():
    e = build()
    assert e.cards[7] == SourceCard("op05", "OP05-098", "L", "", "")
    assert 9 not in e.cards


def test_unknown_code_dons_and_non_singles_skipped():
    e = build()
    assert 5 not in e.cards and 6 not in e.cards and 8 not in e.cards


def test_sets():
    e = build()
    assert e.sets["op05"] == SourceSet("op05", "Awakening of the New Era", None)
    assert e.sets["eb02"] == SourceSet("eb02", "Anime 25th Collection", None)


def test_set_name_falls_back_to_raw_title():
    packs = {"1": {"id": "1", "raw_title": "Promotion card", "title_parts": {}}}
    assert build(cards={}, packs=packs).sets == {"pk1": SourceSet("pk1", "Promotion card", None)}


def test_rarity_passthrough():
    e = build(cards={"OP05-001": card("OP05-001", "569105", "Mystery")})
    assert e.cards[1].rarity == "Mystery"


def test_missing_rarity_is_its_own_value():
    cards = {"OP05-060": {**card("OP05-060", "569105", "Rare"), "rarity": None},
             "OP05-060_p1": card("OP05-060_p1", "569202", "Rare")}
    products = [prod(9, "B (OP05-060)", 20)]
    assert build(cards=cards, products=products).cards == {}


def test_empty_files():
    assert build(cards={}, packs={}).cards == {}
    e = build(cards=[], packs=[])
    assert e.cards == {} and e.sets == {} and e.expansions == {}


def client(status=200, content=None):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if content is not None:
            return httpx.Response(status, content=content)
        if str(request.url) == punkrecords.PACKS_URL:
            return httpx.Response(status, json=PACKS)
        if str(request.url) == punkrecords.CARDS_URL:
            return httpx.Response(status, json=CARDS)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_fetch_urls():
    c, seen = client()
    e = punkrecords.fetch(c, PRODUCTS, SINGLES)
    assert sorted(seen) == sorted([punkrecords.PACKS_URL, punkrecords.CARDS_URL])
    assert e.expansions == {10: "op05"}


def test_http_error():
    c, _ = client(status=500)
    with pytest.raises(SourceError, match="punk-records"):
        punkrecords.fetch(c, PRODUCTS, SINGLES)


def test_bad_json():
    c, _ = client(content=b"not json")
    with pytest.raises(SourceError, match="punk-records"):
        punkrecords.fetch(c, PRODUCTS, SINGLES)
