"""TCGdex's public API for Pokémon (English and Japanese): one row per printing, stamped variants included.

TCGdex has no bulk file: a card is read with one request each. What was read is cached in a local folder
(`<cache>/tcgdex/{lang}.json`) so a run only asks for new cards, and for cards still without an image once they are
a week old. TCG Pocket (serie `tcgp`) is a digital game: its sets are dropped before any card is read.

A card's `variants_detailed` lists its printings; a stamped one (a set-logo, a staff or a 1st edition stamp) is a row
of its own with the card's image (1st edition excepted), the others only add their finish to the card.

An English card's `languages` are `en` plus every other international language whose set details list the card id
(TCGdex's translations use the English set and card ids). Those languages are read from their set lists and set
details only, never card by card; set details are cached in `<cache>/tcgdex/{lang}-sets.json` and read again once
they are a week old. A language that fails is left out of this run with a warning.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx

from .. import http
from ..model import CardSet, Row, SourceData, SourceError, finish, normalize_number, row_id, slug

logger = logging.getLogger(__name__)

BASE = "https://api.tcgdex.net/v2"
MAX_WORKERS = 4
RETRY_AFTER = timedelta(days=7)
SAVE_EVERY = 1000  # cards between cache saves and progress lines
POCKET = "tcgp"  # TCGdex serie id of TCG Pocket
FIRST_EDITION = "1st-edition"  # stamp label of 1st edition printings
# TCGdex's international languages (same set and card ids as English); the Asian ones are regional sets of their own
INTERNATIONAL = ("fr", "de", "it", "es", "es-mx", "pt", "pt-br", "pt-pt", "nl", "pl", "ru")


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
    """New cards, cards cached before finishes were kept (v1 cache), and once a week the cards still without an
    image."""
    if entry is None or "finishes" not in entry:
        return True
    if entry.get("image"):
        return False
    return now - datetime.fromisoformat(entry["fetched_at"]) > RETRY_AFTER


def _read_card(client, lang, card_id, set_id, now):
    """The card's cache entry. Its finishes are the true `variants` flags plus the type of every unstamped
    `variants_detailed` entry; a stamped entry is a printing of its own, labelled by its stamp names. The 1st edition
    stamp is a finish, and a stamp whose label slugs to nothing makes no row (it would take the card's id)."""
    data = _get(client, f"{BASE}/{lang}/cards/{quote(card_id, safe='')}", missing_ok=True)
    if data is None:
        return None
    finishes = {finish(k) for k, v in (data.get("variants") or {}).items() if v is True}
    stamps: dict[str, set[str]] = {}
    for v in data.get("variants_detailed") or []:
        kind = finish(v["type"]) if v.get("type") else ""
        label = "-".join(filter(None, (slug(str(name)) for name in v.get("stamp") or [])))
        if label == FIRST_EDITION:  # a finish, already a variant flag of the card
            finishes.add("first_edition")
            label = ""
        if label:
            stamps.setdefault(label, set()).update({kind} - {""})
        elif kind:
            finishes.add(kind)
    return {
        "rarity": data.get("rarity") or "",
        "local_id": str(data.get("localId") or ""),
        "image": data.get("image") or "",
        "name": data.get("name") or "",
        "set": set_id,
        "finishes": sorted(finishes),
        "stamps": sorted(stamps),
        "stamp_finishes": {label: sorted(kinds) for label, kinds in stamps.items()},
        "fetched_at": now.isoformat(),
    }


def _save_cache(cache_dir, lang, cache):
    path = _cache_path(cache_dir, lang)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")


def _fetch_language(client, cache_dir, lang, now):
    """The sets of a language (TCG Pocket left out) and their cards' cache entries.

    The cache is saved every `SAVE_EVERY` cards and on the way out (errors and Ctrl+C included), so an interrupted
    run resumes where it stopped; on Ctrl+C the queued requests are cancelled instead of waited for."""
    pool = ThreadPoolExecutor(MAX_WORKERS)
    cache = None
    try:
        listing = _get(client, f"{BASE}/{lang}/sets")
        details = []
        pocket = 0
        for s, d in zip(listing, pool.map(
            lambda s: _get(client, f"{BASE}/{lang}/sets/{quote(s['id'], safe='')}", missing_ok=True), listing
        )):
            if d is None:
                logger.warning("TCGdex: %s set %s not found, skipped", lang, s["id"])
            elif (d.get("serie") or {}).get("id") == POCKET:
                pocket += 1
            else:
                details.append(d)
        cache = _load_cache(cache_dir, lang)
        wanted = [
            (c["id"], d["id"]) for d in details for c in d.get("cards") or [] if _needs_fetch(cache.get(c["id"]), now)
        ]
        logger.info("TCGdex: %s sets=%d (TCG Pocket skipped=%d) cards to fetch=%d (cached=%d)",
                    lang, len(details), pocket, len(wanted), len(cache))
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


def _set_is_stale(entry, now):
    """Cached set details are read again when missing, malformed or `RETRY_AFTER` old."""
    try:
        return not isinstance(entry["cards"], list) or now - datetime.fromisoformat(entry["fetched_at"]) > RETRY_AFTER
    except (KeyError, TypeError, ValueError):
        return True


def _fetch_translations(client, cache_dir, lang, set_ids, now):
    """The ids of the cards each of `set_ids` lists in `lang` (sets the language lacks are left out), or None when a
    request fails. Set details are cached and read again once they are `RETRY_AFTER` old; what was read is saved on
    the way out, so a failed run keeps it."""
    pool = ThreadPoolExecutor(MAX_WORKERS)
    cache_key = f"{lang}-sets"
    cache = _load_cache(cache_dir, cache_key)
    try:
        listed = {s["id"] for s in _get(client, f"{BASE}/{lang}/sets")}
        wanted = [i for i in set_ids if i in listed and _set_is_stale(cache.get(i), now)]
        for set_id, d in zip(wanted, pool.map(
            lambda i: _get(client, f"{BASE}/{lang}/sets/{quote(i, safe='')}", missing_ok=True), wanted
        )):
            cache[set_id] = {"cards": [c["id"] for c in (d or {}).get("cards") or []], "fetched_at": now.isoformat()}
        logger.info("TCGdex: %s sets=%d (read=%d)", lang, len(listed & set(set_ids)), len(wanted))
        return {i: set(cache[i]["cards"]) for i in set_ids if i in listed}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning("TCGdex: %s left out of languages: %s: %s", lang, type(exc).__name__, exc)
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        _save_cache(cache_dir, cache_key, cache)


def _rows(lang, details, cache, translations=None):
    """`translations`: extra language -> set id -> card ids, for the English rows' languages."""
    translations = translations or {}
    sets, rows = [], []
    for d in details:
        code = d["id"].lower() + ("-ja" if lang == "ja" else "")
        released = date.fromisoformat(d["releaseDate"]) if d.get("releaseDate") else None
        sets.append(CardSet(code, d["name"], released, "ja" if lang == "ja" else ""))
        for c in d.get("cards") or []:
            entry = cache.get(c["id"])
            if not entry:
                continue
            number = normalize_number(entry.get("local_id"))
            languages = ("ja",) if lang == "ja" else tuple(sorted({"en"} | {
                t for t, cards in translations.items() if c["id"] in cards.get(d["id"], ())}))
            image = entry.get("image") or ""
            base = Row(
                id=row_id("pokemon", code, number), kind="card", name=entry.get("name") or "", set_code=code,
                number=number, variant="", rarity=entry.get("rarity") or "", languages=languages,
                finishes=tuple(entry["finishes"]), category="",
                image_source=(image + "/high.webp")[:500] if image else "", source_key=f"{lang}:{c['id']}",
            )
            rows.append(base)
            stamp_finishes = entry.get("stamp_finishes") or {}
            for label in entry.get("stamps") or []:
                rows.append(replace(
                    base, id=row_id("pokemon", code, number, label), variant=label,
                    finishes=tuple(stamp_finishes.get(label) or base.finishes), source_key=f"{base.source_key}:{label}",
                ))
    return sets, rows


def fetch(client: httpx.Client, cache_dir: Path, langs=("en", "ja"), now: datetime | None = None,
          extra_langs=INTERNATIONAL) -> SourceData:
    """Pokémon rows and sets, one language after the other. Set codes are TCGdex's set ids lower-cased, with "-ja"
    after the Japanese ones (TCGdex's English and Japanese ids overlap: sv8 and SV8). With English, `extra_langs`
    add their language to the English cards they list."""
    now = now or datetime.now(UTC)
    fetched = {lang: _fetch_language(client, cache_dir, lang, now) for lang in langs}
    translations = {}
    if "en" in fetched:
        set_ids = [d["id"] for d in fetched["en"][0]]
        for extra in extra_langs:
            cards = _fetch_translations(client, cache_dir, extra, set_ids, now)
            if cards is not None:
                translations[extra] = cards
    sets, rows = [], []
    try:
        for lang, (details, cache) in fetched.items():
            lang_sets, lang_rows = _rows(lang, details, cache, translations if lang == "en" else None)
            sets += lang_sets
            rows += lang_rows
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"TCGdex: {type(exc).__name__}: {exc}") from exc
    return SourceData(rows=rows, sets=sets)
