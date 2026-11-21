import httpx
import pytest

from cardledger_database.matcher import Group, Printing
from cardledger_database.model import SourceError
from cardledger_database.sources import yamlyugi

IMG = "https://img.yugipedia.com/7/73/TrickstarLycoris-OP09-EN-UtR-UE.png"

CARDS = [
    {"name": {"en": "Trickstar Lycoris", "ja": "x"},
     "images": [{"index": 1, "image": "TrickstarLycoris-OP09-EN-UtR-UE.png"}],
     "sets": {"en": [{"set_number": "OP09-EN001", "set_name": "OTS Tournament Pack 9", "rarities": ["Ultimate Rare", "Super Rare"]},
                     {"set_number": "COTD-EN002", "set_name": "Code of the Duelist", "rarities": ["Common"]}],
              "ja": [{"set_number": "X-JP001", "set_name": "Japanese only", "rarities": ["Common"]}]}},
    {"name": {"en": "Two Arts"},
     "images": [{"index": 1, "image": "A.png"}, {"index": 2, "image": "B.png"}],
     "sets": {"en": [{"set_number": "COTD-EN003", "set_name": "Code of the Duelist"},
                     {"set_number": "COTD-ENSE1", "set_name": "Code of the Duelist", "rarities": ["Secret Rare"]}]}},
    {"name": {"ja": "Japanese card"}, "sets": {"en": [{"set_number": "Z-EN1", "set_name": "Zeta", "rarities": ["Common"]}]}},
    {"name": {"en": "No sets"}, "sets": None},
    {"name": {"en": "No English sets"}, "images": [], "sets": {"ja": []}},
]


def test_image_url():
    assert yamlyugi.image_url("TrickstarLycoris-OP09-EN-UtR-UE.png") == IMG


def test_groups():
    groups, _ = yamlyugi.parse(CARDS)
    assert groups == [Group(1, "Code of the Duelist", "COTD", None), Group(2, "OTS Tournament Pack 9", "OP09", None)]


def test_printings():
    _, printings = yamlyugi.parse(CARDS)
    rows = [(p.group_id, p.name, p.number, p.rarity, p.image_url) for p in printings]
    assert rows == [
        (2, "Trickstar Lycoris", "OP09-EN001", "Ultimate Rare", IMG),
        (2, "Trickstar Lycoris", "OP09-EN001", "Super Rare", IMG),
        (1, "Trickstar Lycoris", "COTD-EN002", "Common", IMG),
        (1, "Two Arts", "COTD-EN003", "", ""),  # no rarities: one printing; two artworks: no image
        (1, "Two Arts", "COTD-ENSE1", "Secret Rare", ""),
    ]
    assert [p.printing_id for p in printings] == [1, 2, 3, 4, 5]
    assert all(p.image_large_url == p.image_url for p in printings)


def test_abbreviation_is_the_most_common_prefix():
    cards = [{"name": {"en": n}, "sets": {"en": [{"set_number": num, "set_name": "S", "rarities": ["C"]}]}}
             for n, num in (("a", "AAA-EN1"), ("b", "BB-EN2"), ("c", "BB-EN3"))]
    assert yamlyugi.parse(cards)[0] == [Group(1, "S", "BB", None)]


def client(status=200, content=None):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=CARDS)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_fetch():
    c, seen = client()
    groups, printings = yamlyugi.fetch(c)
    assert seen == [yamlyugi.URL] and len(groups) == 2 and len(printings) == 5


def test_http_error():
    c, _ = client(status=404)
    with pytest.raises(SourceError, match="yaml-yugi"):
        yamlyugi.fetch(c)


def test_bad_json():
    c, _ = client(content=b"not json")
    with pytest.raises(SourceError, match="yaml-yugi"):
        yamlyugi.fetch(c)
