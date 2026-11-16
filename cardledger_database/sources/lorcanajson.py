"""LorcanaJSON's public card data for Lorcana: set code, number, rarity and images, keyed by Cardmarket id."""

from datetime import date

import httpx

from ..errors import SourceError
from ..model import Enrichment, SourceCard, SourceSet, cardmarket_id, normalize_number

URL = "https://lorcanajson.org/files/current/en/allCards.json"


def fetch(client: httpx.Client) -> Enrichment:
    """The Lorcana enrichment: cards with a Cardmarket id (the first one wins) and every set."""
    cards: dict[int, SourceCard] = {}
    sets: dict[str, SourceSet] = {}
    try:
        response = client.get(URL)
        response.raise_for_status()
        data = response.json()
        for code, s in data["sets"].items():
            released = date.fromisoformat(s["releaseDate"]) if s.get("releaseDate") else None
            sets[str(code).lower()] = SourceSet(str(code).lower(), s["name"], released)
        for card in data["cards"]:
            cm_id = cardmarket_id((card.get("externalLinks") or {}).get("cardmarketId"))
            if not cm_id or cm_id in cards:  # missing or not an integer: skipped
                continue
            images = card.get("images") or {}
            cards[cm_id] = SourceCard(
                str(card["setCode"]).lower(), normalize_number(str(card["number"])), card.get("rarity") or "",
                (images.get("thumbnail") or "")[:500], (images.get("full") or "")[:500],
            )
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"LorcanaJSON: {type(exc).__name__}: {exc}") from exc
    return Enrichment(cards=cards, sets=sets)
