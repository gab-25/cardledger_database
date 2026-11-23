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
    assert groups == [Group(1, "Code of the Duelist", "COTD", None), Group(2, "OTS Tournament Pack 9", "OP09", None),
                      Group(3, "Japanese only", "X-JP", None, "ja")]


def test_japanese_set_with_an_english_name_is_its_own_group():
    cards = [{"name": {"en": "Card"}, "sets": {
        "en": [{"set_number": "SAST-EN001", "set_name": "Savage Strike", "rarities": ["Common"]}],
        "ja": [{"set_number": "SAST-JP001", "set_name": "Savage Strike", "rarities": ["Super Rare"]}]}}]
    groups, printings = yamlyugi.parse(cards)
    assert groups == [Group(1, "Savage Strike", "SAST", None), Group(2, "Savage Strike", "SAST-JP", None, "ja")]
    assert [(p.group_id, p.name, p.number, p.rarity) for p in printings] == [
        (1, "Card", "SAST-EN001", "Common"), (2, "Card", "SAST-JP001", "Super Rare")]


def test_printings():
    _, printings = yamlyugi.parse(CARDS)
    rows = [(p.group_id, p.name, p.number, p.rarity, p.image_url) for p in printings]
    assert rows == [
        (2, "Trickstar Lycoris", "OP09-EN001", "Ultimate Rare", IMG),
        (2, "Trickstar Lycoris", "OP09-EN001", "Super Rare", IMG),
        (1, "Trickstar Lycoris", "COTD-EN002", "Common", IMG),
        (1, "Two Arts", "COTD-EN003", "", ""),  # no rarities: one printing; two artworks: no image
        (1, "Two Arts", "COTD-ENSE1", "Secret Rare", ""),
        (3, "Trickstar Lycoris", "X-JP001", "Common", IMG),
    ]
    assert [p.printing_id for p in printings] == [1, 2, 3, 4, 5, 6]
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
    assert seen == [yamlyugi.URL] and len(groups) == 3 and len(printings) == 6


def test_http_error():
    c, _ = client(status=404)
    with pytest.raises(SourceError, match="yaml-yugi"):
        yamlyugi.fetch(c)


def test_bad_json():
    c, _ = client(content=b"not json")
    with pytest.raises(SourceError, match="yaml-yugi"):
        yamlyugi.fetch(c)
