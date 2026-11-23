"""TCGdex's public API for Pokémon (English and Japanese): set code, number, rarity and images, keyed by Cardmarket id.

TCGdex has no bulk file: a card is read with one request each. What was read is cached in a local folder
(`<cache>/tcgdex/{lang}.json`) so a run only asks for new cards, and for cards still without a
Cardmarket id or without an image once they are a week old.

A card has a Cardmarket id of its own and one per variant (`variants_detailed`): Cardmarket often sells a card's
stamped or special-foil versions as separate products. The variants show the card's image and number.
"""

import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx

from .. import http
from ..model import Enrichment, SourceCard, SourceError, SourceSet, cardmarket_id, normalize_number

logger = logging.getLogger(__name__)

BASE = "https://api.tcgdex.net/v2"
MAX_WORKERS = 4
RETRY_AFTER = timedelta(days=7)
SAVE_EVERY = 1000  # cards between cache saves and progress lines


def _get(client, url, *, missing_ok=False):
    response = http.get(client, url)
    if missing_ok and response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _cache_path(cache_dir: Path, lang: str) -> Path:
    return cache_dir / "tcgdex" / f"{lang}.json"


def _load_cache(cache_dir, lang):
    path = _cache_path(cache_dir, lang)
    if not path.is_file():
        return {}
    try:
        cache = json.loads(path.read_bytes())
    except ValueError:
        return {}
    return cache if isinstance(cache, dict) else {}


def _needs_fetch(entry, now):
    """New cards, cards cached before variant ids were kept, and once a week the cards still without a Cardmarket id
    or without an image."""
    if entry is None or "variant_cms" not in entry:
        return True
    if cardmarket_id(entry.get("cm")) is not None and entry.get("image"):
        return False
    return now - datetime.fromisoformat(entry["fetched_at"]) > RETRY_AFTER


def _read_card(client, lang, card_id, set_id, now):
    data = _get(client, f"{BASE}/{lang}/cards/{quote(card_id, safe='')}", missing_ok=True)
    if data is None:
        return None
    cardmarket = (data.get("pricing") or {}).get("cardmarket") or {}
    variant_cms = [cardmarket_id((v.get("thirdParty") or {}).get("cardmarket"))
                   for v in data.get("variants_detailed") or []]
    return {
        "cm": cardmarket_id(cardmarket.get("idProduct")),
        "variant_cms": sorted({cm for cm in variant_cms if cm is not None}),
        "rarity": data.get("rarity") or "",
        "local_id": str(data.get("localId") or ""),
        "image": data.get("image") or "",
        "set": set_id,
        "fetched_at": now.isoformat(),
    }


def _save_cache(cache_dir, lang, cache):
    path = _cache_path(cache_dir, lang)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")


def _fetch_language(client, cache_dir, lang, now):
    """The sets of a language (id, name, date, official abbreviation) and their cards' cache entries.

    The cache is saved every `SAVE_EVERY` cards and on the way out (errors and Ctrl+C included), so an interrupted
    run resumes where it stopped; on Ctrl+C the queued requests are cancelled instead of waited for."""
    pool = ThreadPoolExecutor(MAX_WORKERS)
    cache = None
    try:
        listing = _get(client, f"{BASE}/{lang}/sets")
        details = []
        for s, d in zip(listing, pool.map(
            lambda s: _get(client, f"{BASE}/{lang}/sets/{quote(s['id'], safe='')}", missing_ok=True), listing
        )):
            if d is None:
                logger.warning("TCGdex: %s set %s not found, skipped", lang, s["id"])
            else:
                details.append(d)
        cache = _load_cache(cache_dir, lang)
        wanted = [
            (c["id"], d["id"]) for d in details for c in d.get("cards") or [] if _needs_fetch(cache.get(c["id"]), now)
        ]
        logger.info("TCGdex: %s sets=%d cards to fetch=%d (cached=%d)", lang, len(details), len(wanted), len(cache))
        futures = [pool.submit(_read_card, client, lang, card_id, set_id, now) for card_id, set_id in wanted]
        failure = None
        for done, ((card_id, _), future) in enumerate(zip(wanted, futures), 1):
            try:
                entry = future.result()
            except Exception as exc:  # keep what the other cards fetched, raise once they are saved
                failure = failure or exc
                continue
            if entry is not None:
                cache[card_id] = entry
            if done % SAVE_EVERY == 0:
                _save_cache(cache_dir, lang, cache)
                logger.info("TCGdex: %s %d/%d cards fetched", lang, done, len(wanted))
        if failure:
            raise failure
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"TCGdex: {type(exc).__name__}: {exc}") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if cache is not None:
            _save_cache(cache_dir, lang, cache)
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


def fetch(client: httpx.Client, cache_dir: Path, langs=("en", "ja"), now: datetime | None = None) -> Enrichment:
    """The Pokémon enrichment: cards with a Cardmarket id (the first one wins, English first) and every set. The
    cards' own ids are assigned before their variants' ids, so a wrong variant id cannot take another card's place."""
    now = now or datetime.now(UTC)
    fetched = {lang: _fetch_language(client, cache_dir, lang, now) for lang in langs}
    codes = _set_codes({lang: details for lang, (details, _) in fetched.items()})
    cards: dict[int, SourceCard] = {}
    sets: dict[str, SourceSet] = {}
    variants: list[tuple[int, SourceCard]] = []
    try:
        for lang, (details, cache) in fetched.items():
            for d in details:
                code = codes[lang][d["id"]]
                released = date.fromisoformat(d["releaseDate"]) if d.get("releaseDate") else None
                sets[code] = SourceSet(code, d["name"], released, "ja" if lang == "ja" else "")
                for c in d.get("cards") or []:
                    entry = cache.get(c["id"])
                    if not entry:
                        continue
                    image = entry.get("image") or ""
                    card = SourceCard(
                        code, normalize_number(entry.get("local_id")), entry.get("rarity") or "",
                        (image + "/low.webp")[:500] if image else "",
                        (image + "/high.webp")[:500] if image else "",
                    )
                    cm = cardmarket_id(entry.get("cm"))
                    if cm and cm not in cards:
                        cards[cm] = card
                    variants.extend((v, card) for v in map(cardmarket_id, entry.get("variant_cms") or []) if v)
        for cm, card in variants:
            cards.setdefault(cm, card)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"TCGdex: {type(exc).__name__}: {exc}") from exc
    return Enrichment(cards=cards, sets=sets)
