"""OpenRift's public Riftbound API (https://openrift.app/developers; code AGPL-3.0, card data and images © Riot
Games), English only: one row per printing grouped by art, with the English printings' finishes as attributes. Images
are served by OpenRift.
"""

import logging
from collections import defaultdict
from datetime import date

import httpx

from .. import http
from ..model import CardSet, Row, SourceData, SourceError, finish, normalize_number, row_id, slug

BASE = "https://openrift.app/api/v1"
CATALOG_URL = f"{BASE}/catalog"
IMAGE_URL = "https://openrift.app/media/cards/{id}-{size}.webp"  # size: 120w, 240w, 400w or full (the largest)
ENGLISH = "EN"  # OpenRift's language code of English printings

logger = logging.getLogger(__name__)


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
    """How good an English printing is as the one shown for its row (lower is better): one with an image, no
    promo/event marker, the normal finish and art, then OpenRift's own order and the id (so the choice does not
    depend on the file's order)."""
    return (
        not printing["images"], bool(printing["markers"]),
        printing["finish"] != "normal", printing["artVariant"] != "normal", printing.get("canonicalRank") or 0,
        printing_id,
    )


def _variant(printing: dict) -> str:
    parts = []
    if printing["artVariant"] != "normal":
        parts.append(printing["artVariant"])
    if printing.get("isSigned"):
        parts.append("signed")
    parts.extend(sorted(printing["markers"]))
    return "-".join(filter(None, (slug(p) for p in parts)))


def fetch(client: httpx.Client) -> SourceData:
    """Rows: the English printings of a set with the same short code, art, signature and markers are one row; a group
    without an English printing has none."""
    try:
        catalog = _json(client, CATALOG_URL)
        sets = [CardSet(s["slug"].lower(), s["name"], _released(s.get("releases") or {})) for s in catalog["sets"]]
        set_codes = {s["id"]: s["slug"].lower() for s in catalog["sets"]}
        rarities = {r["slug"]: r["label"] for r in catalog["enums"]["rarities"]}
        cards = catalog["cards"]
        printings = catalog["printings"]
        if not isinstance(printings, dict):
            raise TypeError(f"printings is a {type(printings).__name__}, not an object")
        groups: dict[tuple, list[tuple[str, dict]]] = defaultdict(list)
        for printing_id, p in printings.items():
            key = (p["setId"], p["shortCode"], p["artVariant"], bool(p.get("isSigned")), tuple(sorted(p["markers"])))
            groups[key].append((printing_id, p))
        rows = []
        no_english = 0
        for (set_id, short_code, *_), members in groups.items():
            members = [m for m in members if m[1]["language"] == ENGLISH]
            if not members:
                no_english += 1
                continue
            printing_id, best = min(members, key=lambda m: _rank(*m))
            set_code = set_codes[set_id]
            number = normalize_number(short_code.partition("-")[2])  # "OGN-007a" -> "7A"
            variant = _variant(best)
            image_id = best["images"][0]["imageId"] if best["images"] else ""
            rows.append(Row(
                id=row_id("riftbound", set_code, number, variant), kind="card", name=cards[best["cardId"]]["name"],
                set_code=set_code, number=number, variant=variant, rarity=rarities.get(best["rarity"], best["rarity"]),
                languages=("en",),
                finishes=tuple(sorted({finish(m["finish"]) for _, m in members})), category="",
                image_source=IMAGE_URL.format(id=image_id, size="full") if image_id else "", source_key=printing_id,
            ))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise SourceError(f"OpenRift: {type(exc).__name__}: {exc}") from exc
    if no_english:
        logger.info("OpenRift: %d groups without an English printing skipped", no_english)
    return SourceData(rows=rows, sets=sets)
