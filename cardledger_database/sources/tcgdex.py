"""TCGdex's public API for Pokémon (English and Japanese): set code, number, rarity and images, keyed by Cardmarket id.

TCGdex has no bulk file: a card is read with one request each. What was read is cached on the store
(`cache/tcgdex/{lang}.json`) so a nightly run only asks for new cards, and for cards still without a
Cardmarket id or without an image once they are a week old.
"""

import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import httpx

from ..errors import SourceError
from ..model import Enrichment, SourceCard, SourceSet, normalize_number
from ..store import Store

logger = logging.getLogger(__name__)

BASE = "https://api.tcgdex.net/v2"
MAX_WORKERS = 4
RETRY_AFTER = timedelta(days=7)


def _get(client, url, *, missing_ok=False):
    response = client.get(url)
    if missing_ok and response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _load_cache(store, lang):
    raw = store.get(f"cache/tcgdex/{lang}.json")
    if raw is None:
        return {}
    try:
        cache = json.loads(raw)
    except ValueError:
        return {}
    return cache if isinstance(cache, dict) else {}


def _cm_id(value) -> int | None:
    """A Cardmarket id as an int (also from "123" or 123.0); None when missing or not an integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _needs_fetch(entry, now):
    """New cards, and once a week the cards still without a Cardmarket id or without an image."""
    if entry is None:
        return True
    if _cm_id(entry.get("cm")) is not None and entry.get("image"):
        return False
    return now - datetime.fromisoformat(entry["fetched_at"]) > RETRY_AFTER


def _read_card(client, lang, card_id, set_id, now):
    data = _get(client, f"{BASE}/{lang}/cards/{quote(card_id, safe='')}", missing_ok=True)
    if data is None:
        return None
    cardmarket = (data.get("pricing") or {}).get("cardmarket") or {}
    return {
        "cm": _cm_id(cardmarket.get("idProduct")),
        "rarity": data.get("rarity") or "",
        "local_id": str(data.get("localId") or ""),
        "image": data.get("image") or "",
        "set": set_id,
        "fetched_at": now.isoformat(),
    }


def _fetch_language(client, store, lang, now):
    """The sets of a language (id, name, date, official abbreviation) and their cards' cache entries."""
    try:
        listing = _get(client, f"{BASE}/{lang}/sets")
        with ThreadPoolExecutor(MAX_WORKERS) as pool:
            details = []
            for s, d in zip(listing, pool.map(
                lambda s: _get(client, f"{BASE}/{lang}/sets/{quote(s['id'], safe='')}", missing_ok=True), listing
            )):
                if d is None:
                    logger.warning("TCGdex: %s set %s not found, skipped", lang, s["id"])
                else:
                    details.append(d)
            cache = _load_cache(store, lang)
            wanted = [
                (c["id"], d["id"]) for d in details for c in d.get("cards") or [] if _needs_fetch(cache.get(c["id"]), now)
            ]
            futures = [pool.submit(_read_card, client, lang, card_id, set_id, now) for card_id, set_id in wanted]
            failure = None
            for (card_id, _), future in zip(wanted, futures):
                try:
                    entry = future.result()
                except Exception as exc:  # keep what the other cards fetched, raise once they are saved
                    failure = failure or exc
                    continue
                if entry is not None:
                    cache[card_id] = entry
        store.put(f"cache/tcgdex/{lang}.json", json.dumps(cache, sort_keys=True).encode())
        if failure:
            raise failure
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"TCGdex: {type(exc).__name__}: {exc}") from exc
    return details, cache


def _set_codes(by_lang):
    """TCGdex set id -> final code per language: the official abbreviation, else (or when shared) the id."""
    abbreviations = Counter()
    for details in by_lang.values():
        for d in details:
            abbreviations[((d.get("abbreviation") or {}).get("official") or d["id"]).lower()] += 1
    codes: dict[str, dict[str, str]] = {}
    used: set[str] = set()
    for lang, details in by_lang.items():
        codes[lang] = {}
        for d in details:
            code = ((d.get("abbreviation") or {}).get("official") or d["id"]).lower()
            if abbreviations[code] > 1:
                code = d["id"].lower()
            if code in used:
                code = f"{code}-{lang}"
            used.add(code)
            codes[lang][d["id"]] = code
    return codes


def fetch(client: httpx.Client, store: Store, langs=("en", "ja"), now: datetime | None = None) -> Enrichment:
    """The Pokémon enrichment: cards with a Cardmarket id (the first one wins, English first) and every set."""
    now = now or datetime.now(UTC)
    fetched = {lang: _fetch_language(client, store, lang, now) for lang in langs}
    codes = _set_codes({lang: details for lang, (details, _) in fetched.items()})
    cards: dict[int, SourceCard] = {}
    sets: dict[str, SourceSet] = {}
    try:
        for lang, (details, cache) in fetched.items():
            for d in details:
                code = codes[lang][d["id"]]
                released = date.fromisoformat(d["releaseDate"]) if d.get("releaseDate") else None
                sets[code] = SourceSet(code, d["name"], released, "ja" if lang == "ja" else "")
                for c in d.get("cards") or []:
                    entry = cache.get(c["id"])
                    cm = _cm_id(entry.get("cm")) if entry else None
                    if not cm or cm in cards:
                        continue
                    image = entry.get("image") or ""
                    cards[cm] = SourceCard(
                        code, normalize_number(entry.get("local_id")), entry.get("rarity") or "",
                        (image + "/low.webp")[:500] if image else "",
                        (image + "/high.webp")[:500] if image else "",
                    )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"TCGdex: {type(exc).__name__}: {exc}") from exc
    return Enrichment(cards=cards, sets=sets)
