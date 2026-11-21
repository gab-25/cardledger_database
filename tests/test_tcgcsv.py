from datetime import date

import httpx
import pytest

from cardledger_database.model import SourceError
from cardledger_database.matcher import Group, Printing
from cardledger_database.sources import tcgcsv


def product(pid, name, number=None, rarity=None):
    extended = []
    if rarity:
        extended.append({"name": "Rarity", "displayName": "Rarity", "value": rarity})
    if number:
        extended.append({"name": "Number", "displayName": "Number", "value": number})
    return {"productId": pid, "name": name, "imageUrl": f"https://cdn/{pid}_200w.jpg", "extendedData": extended}


def make(routes):
    def handler(request):
        path = request.url.path
        if path not in routes:
            return httpx.Response(404)
        return httpx.Response(200, json={"success": True, "errors": [], "results": routes[path]})

    return httpx.Client(transport=httpx.MockTransport(handler))


GROUPS = [
    {"groupId": 3188, "name": "Romance Dawn", "abbreviation": "OP01", "publishedOn": "2022-12-02T00:00:00"},
    {"groupId": 9, "name": "Promos", "abbreviation": None},
]


def test_groups_and_singles_are_read():
    client = make({
        "/tcgplayer/68/groups": GROUPS,
        "/tcgplayer/68/3188/products": [product(1, "Sabo", "op05-007", "SR"), product(2, "Chopper", "CPB-001 MR")],
        "/tcgplayer/68/9/products": [product(3, "Promo", "P-001")],
    })
    groups, products = tcgcsv.fetch(client, 68)
    assert groups == [
        Group(3188, "Romance Dawn", "OP01", date(2022, 12, 2)),
        Group(9, "Promos", "", None),
    ]
    assert sorted(products, key=lambda p: p.printing_id) == [
        Printing(1, 3188, "Sabo", "OP05-007", "SR", "https://cdn/1_200w.jpg", "https://cdn/1_in_1000x1000.jpg"),
        Printing(2, 3188, "Chopper", "CPB-001", "", "https://cdn/2_200w.jpg", "https://cdn/2_in_1000x1000.jpg"),
        Printing(3, 9, "Promo", "P-001", "", "https://cdn/3_200w.jpg", "https://cdn/3_in_1000x1000.jpg"),
    ]


def test_products_without_number_are_dropped():
    client = make({
        "/tcgplayer/68/groups": GROUPS[:1],
        "/tcgplayer/68/3188/products": [product(1, "Booster Box"), product(2, "Sabo", "OP05-007")],
    })
    _, products = tcgcsv.fetch(client, 68)
    assert [p.printing_id for p in products] == [2]


def test_http_error_raises_source_error():
    client = make({"/tcgplayer/68/groups": GROUPS[:1]})
    with pytest.raises(SourceError):
        tcgcsv.fetch(client, 68)


def test_transient_errors_are_retried():
    routes = {"/tcgplayer/68/groups": GROUPS[:1], "/tcgplayer/68/3188/products": [product(2, "Sabo", "OP05-007")]}
    failed = set()

    def handler(request):
        path = request.url.path
        if path not in failed:  # every path fails once
            failed.add(path)
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json={"success": True, "errors": [], "results": routes[path]})

    _, products = tcgcsv.fetch(httpx.Client(transport=httpx.MockTransport(handler)), 68)
    assert [p.printing_id for p in products] == [2]
