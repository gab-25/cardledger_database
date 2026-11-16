"""Scryfall's public card data for Magic: set code, collector number, rarity and images, keyed by Cardmarket id.

The Default Cards file is ~80 MB of gzip JSON Lines: it is decompressed and parsed one line at a time, keeping
only the fields the catalog uses. Also returns the TCGplayer ids of each card, the truth for the matcher bench.
"""

import json
import zlib
from datetime import date

import httpx

from ..errors import SourceError
from ..model import Enrichment, SourceCard, SourceSet, normalize_number

BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
SETS_URL = "https://api.scryfall.com/sets"
BULK_TYPE = "default_cards"


def _lines(response):
    """The decompressed lines of a gzip response body, read as it arrives."""
    inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)
    pending = b""
    for chunk in response.iter_bytes():
        pending += inflater.decompress(chunk)
        *lines, pending = pending.split(b"\n")
        yield from lines
    pending += inflater.flush()
    if pending:
        yield pending


def _images(card):
    images = card.get("image_uris")
    if not images and card.get("card_faces"):
        images = card["card_faces"][0].get("image_uris")
    images = images or {}
    return (images.get("small") or "")[:500], (images.get("normal") or "")[:500]


def _fetch_cards(client):
    cards: dict[int, SourceCard] = {}
    truth: dict[int, set[int]] = {}
    try:
        index = client.get(BULK_DATA_URL)
        index.raise_for_status()
        uri = next(d["jsonl_download_uri"] for d in index.json()["data"] if d["type"] == BULK_TYPE)
        with client.stream("GET", uri) as response:
            response.raise_for_status()
            for line in _lines(response):
                if not line.strip():
                    continue
                card = json.loads(line)
                cm_id = card.get("cardmarket_id")
                if not cm_id:
                    continue
                ids = {card[k] for k in ("tcgplayer_id", "tcgplayer_etched_id") if card.get(k) is not None}
                if ids:
                    truth.setdefault(cm_id, set()).update(ids)
                if cm_id in cards:
                    continue
                cards[cm_id] = SourceCard(
                    card["set"].lower(), normalize_number(card.get("collector_number")), card.get("rarity") or "",
                    *_images(card),
                )
    except (httpx.HTTPError, zlib.error, ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        raise SourceError(f"Scryfall cards: {type(exc).__name__}: {exc}") from exc
    return cards, truth


def _fetch_sets(client):
    sets: dict[str, SourceSet] = {}
    url = SETS_URL
    try:
        while url:
            response = client.get(url)
            response.raise_for_status()
            page = response.json()
            for s in page["data"]:
                released = date.fromisoformat(s["released_at"]) if s.get("released_at") else None
                sets[s["code"].lower()] = SourceSet(s["code"].lower(), s["name"], released)
            url = page.get("next_page") if page.get("has_more") else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"Scryfall sets: {type(exc).__name__}: {exc}") from exc
    return sets


def fetch(client: httpx.Client) -> tuple[Enrichment, dict[int, set[int]]]:
    """The Magic enrichment and {Cardmarket id: {TCGplayer ids}} (no None) of every Scryfall card."""
    cards, truth = _fetch_cards(client)
    return Enrichment(cards=cards, sets=_fetch_sets(client)), truth
