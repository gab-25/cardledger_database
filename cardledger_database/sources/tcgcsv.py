"""TCGCSV (a public mirror of TCGplayer's catalog): groups (sets) and single cards for a TCGplayer category."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import httpx

from .. import http
from ..model import SourceError

BASE = "https://tcgcsv.com/tcgplayer"
MAX_WORKERS = 4


@dataclass(frozen=True)
class TcgGroup:
    group_id: int
    name: str
    abbreviation: str
    published_on: date | None


@dataclass(frozen=True)
class TcgProduct:
    product_id: int
    group_id: int
    name: str
    number: str
    rarity: str
    image_url: str


def _results(client, url):
    response = http.get(client, url)
    response.raise_for_status()
    return response.json()["results"]


def _extended(product, name):
    for item in product.get("extendedData") or []:
        if item.get("name") == name:
            return str(item.get("value") or "")
    return ""


def _group(raw):
    published = raw.get("publishedOn")
    return TcgGroup(
        raw["groupId"], raw["name"], raw.get("abbreviation") or "",
        date.fromisoformat(published[:10]) if published else None,
    )


def _products(client, category_id, group_id):
    products = []
    for raw in _results(client, f"{BASE}/{category_id}/{group_id}/products"):
        tokens = _extended(raw, "Number").split()
        if not tokens:  # sealed products have no number
            continue
        products.append(TcgProduct(
            raw["productId"], group_id, raw["name"], tokens[0].upper(),
            _extended(raw, "Rarity"), raw.get("imageUrl") or "",
        ))
    return products


def fetch(client: httpx.Client, category_id: int) -> tuple[list[TcgGroup], list[TcgProduct]]:
    """The groups of a category and their single cards (products with a Number), 4 requests at a time."""
    try:
        groups = [_group(raw) for raw in _results(client, f"{BASE}/{category_id}/groups")]
        with ThreadPoolExecutor(MAX_WORKERS) as pool:
            futures = [pool.submit(_products, client, category_id, g.group_id) for g in groups]
            products: list[TcgProduct] = []
            failure = None
            for future in futures:
                try:
                    products.extend(future.result())
                except Exception as exc:  # collect every result, raise the first failure
                    failure = failure or exc
        if failure:
            raise failure
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"TCGCSV: {type(exc).__name__}: {exc}") from exc
    return groups, products
