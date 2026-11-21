"""Riftcodex's Riftbound API (fan project, no licence stated; images from Riot's CDN): sets and card printings, as
matcher input. The API is slow, so the card pages are fetched 4 at a time with a long timeout."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx

from .. import http
from ..matcher import Group, Printing
from ..model import SourceError

BASE = "https://api.riftcodex.com"
PAGE_SIZE = 100  # the API's maximum
MAX_WORKERS = 4
TIMEOUT = 300  # seconds per request: single pages took up to 106 s on 2026-11-21


def parse_sets(items: list) -> list[Group]:
    """One group per set, ids by sorted set id."""
    groups = []
    for i, s in enumerate(sorted(items, key=lambda s: s["set_id"]), start=1):
        published = s.get("published_on")
        groups.append(Group(i, s["name"], s["set_id"], date.fromisoformat(published[:10]) if published else None))
    return groups


def _number(card: dict) -> str:
    """The number in the Riftbound id ("ven-r04" -> "R04", "ven-004-166" -> "004", "unl-229*-219" -> "229*"):
    `collector_number` alone is ambiguous (4 for the rune r04, the card 004 and the showcase sp4)."""
    parts = (card.get("riftbound_id") or "").split("-")
    return parts[1].upper() if len(parts) >= 2 and parts[1] else str(card["collector_number"])


def _latest(items: list) -> list:
    """One record per Riftbound id (Riftcodex has duplicates), the most recently updated, in first-seen order."""
    kept: dict[str, dict] = {}
    order: list[str] = []
    for i, card in enumerate(items):
        key = card.get("riftbound_id") or f"#{i}"
        updated = (card.get("metadata") or {}).get("updated_on") or ""
        if key not in kept:
            order.append(key)
        elif updated <= ((kept[key].get("metadata") or {}).get("updated_on") or ""):
            continue
        kept[key] = card
    return [kept[k] for k in order]


def parse_cards(items: list, groups: list[Group]) -> tuple[list[Printing], list[Group]]:
    """One printing per card (duplicates dropped), and the groups plus one for each card set missing from them.
    Cardmarket writes "Miss Fortune, Buccaneer" where Riftcodex writes "Miss Fortune - Buccaneer" (the matcher drops
    " - ...")."""
    groups = list(groups)
    ids = {g.abbreviation: g.group_id for g in groups}
    printings = []
    for card in _latest(items):
        set_id = card["set"]["set_id"]
        if set_id not in ids:
            ids[set_id] = len(groups) + 1
            groups.append(Group(ids[set_id], card["set"].get("label") or set_id, set_id, None))
        image = (card.get("media") or {}).get("image_url") or ""
        printings.append(Printing(
            len(printings) + 1, ids[set_id], card["name"].replace(" - ", ", "), _number(card),
            (card.get("classification") or {}).get("rarity") or "", image, image,
        ))
    return printings, groups


def _json(client, path):
    response = http.get(client, f"{BASE}{path}", timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _cards_page(client, page):
    return _json(client, f"/cards?size={PAGE_SIZE}&page={page}")


def fetch(client: httpx.Client) -> tuple[list[Group], list[Printing]]:
    try:
        groups = parse_sets(_json(client, f"/sets?size={PAGE_SIZE}")["items"])
        first = _cards_page(client, 1)
        items = list(first["items"])
        with ThreadPoolExecutor(MAX_WORKERS) as pool:
            for page in pool.map(lambda n: _cards_page(client, n), range(2, int(first["pages"]) + 1)):
                items.extend(page["items"])
        if "total" in first and len(items) != int(first["total"]):
            raise ValueError(f"{len(items)} cards read, {first['total']} announced")
        printings, groups = parse_cards(items, groups)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"Riftcodex: {type(exc).__name__}: {exc}") from exc
    return groups, printings
