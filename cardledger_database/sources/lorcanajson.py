"""LorcanaJSON's public card data for Lorcana, English only: one row per printing of the English file."""

import logging
from datetime import date

import httpx

from ..model import CardSet, Row, SourceData, SourceError, finish, normalize_number, row_id

URL = "https://lorcanajson.org/files/current/{lang}/allCards.json"

logger = logging.getLogger(__name__)


def _load(client: httpx.Client, lang: str) -> dict:
    response = client.get(URL.format(lang=lang))
    response.raise_for_status()
    data = response.json()
    data["cards"], data["sets"]  # shape check
    return data


def _finishes(card: dict) -> tuple[str, ...]:
    # LorcanaJSON's foilTypes: "None" (non-foil), "Silver", "Satin", "Magma", ...
    return tuple(sorted({"non_foil" if t == "None" else finish(t) for t in card.get("foilTypes") or []}))


def fetch(client: httpx.Client) -> SourceData:
    """Rows from the English file."""
    try:
        english = _load(client, "en")
        sets = []
        for code, s in english["sets"].items():
            released = date.fromisoformat(s["releaseDate"]) if s.get("releaseDate") else None
            sets.append(CardSet(str(code).lower(), s["name"], released))
        rows = []
        no_number = 0
        for card in english["cards"]:
            if card.get("number") is None or not str(card["number"]).strip():
                no_number += 1  # no collector number, no stable id
                continue
            # Promos keep their base setCode but carry a promoGrouping (P1, D23, ...): that is their own set.
            set_code = str(card.get("promoGrouping") or card["setCode"]).lower()
            number = normalize_number(str(card["number"]))
            variant = str(card.get("variant") or "").lower()
            rows.append(Row(
                id=row_id("lorcana", set_code, number, variant), kind="card", name=card["fullName"], set_code=set_code,
                number=number, variant=variant, rarity=card.get("rarity") or "",
                languages=("en",),
                finishes=_finishes(card), category="",
                image_source=((card.get("images") or {}).get("full") or "")[:500], source_key=str(card["id"]),
            ))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"LorcanaJSON: {type(exc).__name__}: {exc}") from exc
    if no_number:
        logger.warning("LorcanaJSON: %d cards without a number skipped", no_number)
    return SourceData(rows=rows, sets=sets)
