"""TCGdex's public API for Pokémon (English and Japanese): set code, number, rarity and images, keyed by Cardmarket id.

TCGdex has no bulk file: a card is read with one request each. What was read is cached on the store
(`cache/tcgdex/{lang}.json`) so a nightly run only asks for new cards, and for cards still without a
Cardmarket id once they are a week old.
"""

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import httpx

from ..errors import SourceError
from ..model import Enrichment, SourceCard, SourceSet, normalize_number
from ..store import Store

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


def _needs_fetch(entry, now):
    if entry is None:
        return True
    if entry.get("cm") is not None:
        return False
    return now - datetime.fromisoformat(entry["fetched_at"]) > RETRY_AFTER


def _read_card(client, lang, card_id, set_id, now):
    data = _get(client, f"{BASE}/{lang}/cards/{card_id}", missing_ok=True)
    if data is None:
        return None
    cardmarket = (data.get("pricing") or {}).get("cardmarket") or {}
    return {
        "cm": cardmarket.get("idProduct"),
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
            details = list(pool.map(lambda s: _get(client, f"{BASE}/{lang}/sets/{s['id']}"), listing))
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
                    if not entry or not entry.get("cm") or entry["cm"] in cards:
                        continue
                    image = entry.get("image") or ""
                    cards[entry["cm"]] = SourceCard(
                        code, normalize_number(entry.get("local_id")), entry.get("rarity") or "",
                        (image + "/low.webp")[:500] if image else "",
                        (image + "/high.webp")[:500] if image else "",
                    )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"TCGdex: {type(exc).__name__}: {exc}") from exc
    return Enrichment(cards=cards, sets=sets)
