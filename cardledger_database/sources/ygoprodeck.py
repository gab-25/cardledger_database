"""YGOPRODeck's API v7 for Yu-Gi-Oh!: one row per TCG set number (as printed, so "LOB-001", "LOB-EN001" and
"LOB-E001" are three rows: the European numbering does not follow the American one) and rarity. Not openly licensed: YGOPRODeck allows
at most 20 requests a second (more blocks the IP for an hour) and asks for its data to be downloaded once and cached
and for its images not to be hotlinked. Both answers are cached for a day, so a run makes two requests at most."""

import json
import logging
import os
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import httpx

from .. import http
from ..model import CardSet, Row, SourceData, SourceError, row_id, slug

CARDS_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
SETS_URL = "https://db.ygoprodeck.com/api/v7/cardsets.php"
CACHE_MAX_AGE = 24 * 3600  # seconds

logger = logging.getLogger(__name__)


def _cards_payload(payload) -> list:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SourceError("YGOPRODeck: cardinfo.php has no 'data' list")
    return payload["data"]


def _sets_payload(payload) -> list:
    if not isinstance(payload, list):
        raise SourceError("YGOPRODeck: cardsets.php is not a list")
    return payload


def _cached(client: httpx.Client, url: str, path: Path, check) -> list:
    """The endpoint's answer, checked by `check`. The cache file is {"downloaded_at": epoch seconds, "payload": ...};
    it is used without a request while younger than a day, and instead of a failed download (with a warning)."""
    cached = None
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(cached["downloaded_at"]) < CACHE_MAX_AGE:
                return check(cached["payload"])
        except (ValueError, KeyError, TypeError, SourceError) as exc:
            logger.warning("YGOPRODeck: ignoring unreadable cache %s: %s", path, exc)
            cached = None
    try:
        response = http.get(client, url)
        response.raise_for_status()
        payload = response.json()
        result = check(payload)
    except (httpx.HTTPError, ValueError, SourceError) as exc:
        if cached is None:
            if isinstance(exc, SourceError):
                raise
            raise SourceError(f"YGOPRODeck: {url}: {type(exc).__name__}: {exc}") from exc
        logger.warning("YGOPRODeck: %s failed (%s: %s), using the cached copy", url, type(exc).__name__, exc)
        return check(cached["payload"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(json.dumps({"downloaded_at": time.time(), "payload": payload}), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return result


def _set_code(number: str) -> str:
    """The set of a printed set number: its part before the first "-" ("LOB-EN001" -> "lob"), or the whole number."""
    return slug(number.split("-", 1)[0])


def _rows(cards: list) -> list[Row]:
    rows = []
    for card in cards:
        images = card.get("card_images") or []
        image = images[0]["image_url"] if len(images) == 1 else ""
        for printing in card.get("card_sets") or []:
            number, rarity = printing["set_code"], printing["set_rarity"]
            set_code = _set_code(number)
            rows.append(Row(
                id=row_id("yugioh", set_code, number, slug(rarity)), kind="card", name=card["name"],
                set_code=set_code, number=number, variant=slug(rarity), rarity=rarity, languages=("en",),
                finishes=(), category="", image_source=image, source_key=f"{card['id']}:{number}:{rarity}",
            ))
    return rows


def _sets(entries: list, used: set[str]) -> list[CardSet]:
    """One set per used prefix with a cardsets.php entry: the alphabetically first name and the earliest TCG date
    of its entries."""
    by_code = defaultdict(list)
    for entry in entries:
        by_code[entry["set_code"].lower()].append(entry)
    sets, several = [], 0
    for code in sorted(used & by_code.keys()):
        group = by_code[code]
        if len({e["set_name"] for e in group}) > 1:
            several += 1
        dates = [date.fromisoformat(e["tcg_date"]) for e in group if e.get("tcg_date")]
        sets.append(CardSet(code, min(e["set_name"] for e in group), min(dates) if dates else None))
    if several:
        logger.warning("YGOPRODeck: %d set prefixes have several cardsets.php names; kept the alphabetically first",
                       several)
    return sets


def fetch(client: httpx.Client, cache_dir: Path) -> SourceData:
    folder = Path(cache_dir) / "ygoprodeck"
    try:
        cards = _cached(client, CARDS_URL, folder / "cardinfo.json", _cards_payload)
        entries = _cached(client, SETS_URL, folder / "cardsets.json", _sets_payload)
        rows = _rows(cards)
        sets = _sets(entries, {r.set_code for r in rows})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise SourceError(f"YGOPRODeck: {type(exc).__name__}: {exc}") from exc
    without_sets = sum(1 for c in cards if not c.get("card_sets"))
    if without_sets:
        logger.info("YGOPRODeck: skipped %d cards without TCG printings", without_sets)
    return SourceData(rows=rows, sets=sets)
