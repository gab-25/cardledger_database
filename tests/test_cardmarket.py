import httpx
import pytest

from cardledger_database.sources.cardmarket import fetch_products
from cardledger_database.model import CmProduct, SourceError

BASE = "https://downloads.s3.cardmarket.com/productCatalog/productList/"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _handler(files):
    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if name not in files:
            return httpx.Response(404)
        return httpx.Response(200, json=files[name])

    return handler


SINGLE = {"idProduct": 10, "name": "Pikachu", "idCategory": 51, "categoryName": "Pokémon Single",
          "idExpansion": 7, "idMetacard": 99}
SEALED = {"idProduct": 20, "name": "Booster Box", "idCategory": 52, "categoryName": "Booster",
          "idExpansion": 7}


def test_fetch_products_reads_singles_and_nonsingles():
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return _handler({
            "products_singles_6.json": {"products": [SINGLE]},
            "products_nonsingles_6.json": {"products": [SEALED]},
        })(request)

    products, single_categories = fetch_products(_client(handler), 6)
    assert urls == [BASE + "products_singles_6.json", BASE + "products_nonsingles_6.json"]
    assert products == [
        CmProduct(10, "Pikachu", 51, "Pokémon Single", 7, 99),
        CmProduct(20, "Booster Box", 52, "Booster", 7, None),
    ]
    assert single_categories == {51}


def test_single_categories_only_from_singles_file():
    files = {
        "products_singles_6.json": {"products": [SINGLE, dict(SINGLE, idProduct=11, idCategory=53)]},
        "products_nonsingles_6.json": {"products": [SEALED]},
    }
    _, single_categories = fetch_products(_client(_handler(files)), 6)
    assert single_categories == {51, 53}


def test_http_error_raises_source_error():
    client = _client(lambda request: httpx.Response(500))
    with pytest.raises(SourceError, match="products_singles_6"):
        fetch_products(client, 6)


def test_missing_field_raises_source_error():
    bad = {k: v for k, v in SINGLE.items() if k != "idExpansion"}
    files = {
        "products_singles_6.json": {"products": [bad]},
        "products_nonsingles_6.json": {"products": []},
    }
    with pytest.raises(SourceError, match="products_singles_6"):
        fetch_products(_client(_handler(files)), 6)


def test_invalid_json_raises_source_error():
    client = _client(lambda request: httpx.Response(200, content=b"not json"))
    with pytest.raises(SourceError):
        fetch_products(client, 6)


def test_fetch_trends():
    from cardledger_database.sources.cardmarket import fetch_trends

    guide = {"priceGuides": [{"idProduct": 1, "trend": 2.5}, {"idProduct": 2, "trend": None}, {"idProduct": 3}]}
    trends = fetch_trends(_client(_handler({"price_guide_18.json": guide})), 18)
    assert trends == {1: 2.5}


@pytest.mark.parametrize("files", [{}, {"price_guide_18.json": {"nothing": []}}, {"price_guide_18.json": [1]}])
def test_fetch_trends_bad_file_raises_source_error(files):
    from cardledger_database.sources.cardmarket import fetch_trends

    with pytest.raises(SourceError, match="Cardmarket price_guide_18.json"):
        fetch_trends(_client(_handler(files)), 18)
