"""OpenRift's public Riftbound API (https://openrift.app/developers; code AGPL-3.0, card data and images © Riot
Games): sets, printings and their Cardmarket product ids. Images are served by OpenRift.

Two documents: the catalog (sets, printings) and the marketplace links (printing id -> Cardmarket productId).
"""

from datetime import date

import httpx

from .. import http
from ..model import Enrichment, SourceCard, SourceError, SourceSet, cardmarket_id

BASE = "https://openrift.app/api/v1"
CATALOG_URL = f"{BASE}/catalog"
LINKS_URL = f"{BASE}/prices/links"
IMAGE_URL = "https://openrift.app/media/cards/{id}-{size}.webp"  # size: 120w, 240w, 400w or full


def _json(client, url):
    response = http.get(client, url)
    response.raise_for_status()
    return response.json()


def _released(releases: dict) -> date | None:
    """The English release date, else the earliest one."""
    dates = {lang: r["releasedAt"] for lang, r in releases.items() if r.get("releasedAt")}
    if not dates:
        return None
    return date.fromisoformat((dates.get("EN") or min(dates.values()))[:10])


def _rank(printing_id: str, printing: dict) -> tuple:
    """How good a printing is as the one shown for its Cardmarket product (lower is better). Cardmarket sells a card's
    languages and finishes as one product: prefer one with an image, then English, no promo/event marker, the normal
    finish and art, then OpenRift's own order and the id (so the choice does not depend on the file's order)."""
    return (
        not printing["images"], printing["language"] != "EN", bool(printing["markers"]),
        printing["finish"] != "normal", printing["artVariant"] != "normal", printing.get("canonicalRank") or 0,
        printing_id,
    )


def _card(printing: dict, set_codes: dict[str, str], rarities: dict[str, str]) -> SourceCard:
    _, _, number = printing["shortCode"].partition("-")  # "OGN-007a" -> "007a"
    image_id = printing["images"][0]["imageId"] if printing["images"] else ""
    image = (lambda size: IMAGE_URL.format(id=image_id, size=size)) if image_id else (lambda size: "")
    return SourceCard(set_codes[printing["setId"]], number, rarities.get(printing["rarity"], printing["rarity"]),
                      image("400w"), image("full"))


def fetch(client: httpx.Client) -> Enrichment:
    """The Riftbound enrichment: one card per Cardmarket product (its best printing) and every set."""
    try:
        catalog = _json(client, CATALOG_URL)
        links = _json(client, LINKS_URL)["links"]
        sets = {s["slug"].lower(): SourceSet(s["slug"].lower(), s["name"], _released(s.get("releases") or {}))
                for s in catalog["sets"]}
        set_codes = {s["id"]: s["slug"].lower() for s in catalog["sets"]}
        rarities = {r["slug"]: r["label"] for r in catalog["enums"]["rarities"]}
        printings = catalog["printings"]
        if not isinstance(printings, dict):
            raise TypeError(f"printings is a {type(printings).__name__}, not an object")
        best: dict[int, tuple[tuple, dict]] = {}
        for printing_id, link in links.items():
            cm_id = cardmarket_id(((link or {}).get("cardmarket") or {}).get("productId"))
            if cm_id is None or printing_id not in printings:
                continue
            rank = _rank(printing_id, printings[printing_id])
            if cm_id not in best or rank < best[cm_id][0]:
                best[cm_id] = (rank, printings[printing_id])
        cards = {cm_id: _card(printing, set_codes, rarities) for cm_id, (_, printing) in best.items()}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise SourceError(f"OpenRift: {type(exc).__name__}: {exc}") from exc
    return Enrichment(cards=cards, sets=sets)
