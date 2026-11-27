"""Scryfall's all cards bulk file for Magic: one object per card per language, folded into one row per printing
(set + collector number) listing its languages. Not openly licensed: Scryfall asks for its bulk files instead of per-card calls and
for its images not to be altered."""

import gzip
import json
import logging
import os
import re
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

from .. import http
from ..model import CardSet, Row, SourceData, SourceError, finish, row_id

BULK_URL = "https://api.scryfall.com/bulk-data"
SETS_URL = "https://api.scryfall.com/sets"
API_HEADERS = {"Accept": "application/json"}  # Scryfall's API requires it; the User-Agent comes from the client
# Layouts that are not game cards: art series cards and the Jumpstart theme ("front") cards.
EXCLUDED_LAYOUTS = frozenset({"art_series", "front_card"})

# Scryfall's language codes that are not ISO/BCP-47; the others are ISO 639 already. Phyrexian has no ISO code.
LANGS = {"zhs": "zh-hans", "zht": "zh-hant", "ph": "x-phyrexian"}

logger = logging.getLogger(__name__)


def iso_lang(lang: str) -> str:
    """Scryfall's `lang` as an ISO/BCP-47 code: the few Scryfall-only codes mapped, the rest lower-cased."""
    lang = lang.lower()
    return LANGS.get(lang, lang)


def _api(client: httpx.Client, url: str) -> dict:
    response = http.get(client, url, headers=API_HEADERS)
    response.raise_for_status()
    return response.json()


def _bulk_file(client: httpx.Client, cache_dir: Path) -> Path:
    """The all cards file, downloaded unless the cached copy has the index's `updated_at`."""
    entry = next(d for d in _api(client, BULK_URL)["data"] if d["type"] == "all_cards")
    url = entry["jsonl_download_uri"]
    folder = Path(cache_dir) / "scryfall"
    path = folder / f"all_cards-{re.sub(r'[^0-9A-Za-z.-]', '-', entry['updated_at'])}.jsonl.gz"
    if path.exists():
        return path
    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        with client.stream("GET", url) as response, open(tmp, "wb") as out:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                out.write(chunk)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    for old in (*folder.glob("all_cards-*"), *folder.glob("default_cards-*")):
        if old != path:
            old.unlink()
    return path


def _image(card: dict) -> str:
    """The card's (or its front face's) `large` image, else `png`, else `normal`. `large` comes first: the hosted
    renditions are at most 745 px wide, while the PNG originals are about 1 MB each."""
    for uris in (card.get("image_uris"), ((card.get("card_faces") or [{}])[0]).get("image_uris")):
        for size in ("large", "png", "normal"):
            if uris and uris.get(size):
                return uris[size]
    return ""


def _rank(card: dict) -> tuple:
    """Which language object of a printing gives the row its fields: English, else the first by lang then id."""
    return (card["lang"] != "en", card["lang"], card["id"])


def _rows(path: Path, digital_sets: set[str], skipped: Counter) -> list[Row]:
    """One row per printing. Per printing only the representative's few fields and the sets of languages and
    finishes are kept, not whole objects: the file holds over half a million of them."""
    printings: dict[tuple[str, str], list] = {}  # (set, number) -> [rank, fields, langs, finishes]
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            if not line.strip():
                continue
            card = json.loads(line)
            if card.get("digital") or "paper" not in (card.get("games") or []):
                skipped["digital"] += 1
                continue
            if card["set"] in digital_sets:
                skipped["digital set"] += 1
                continue
            if card.get("layout") in EXCLUDED_LAYOUTS:
                skipped[card["layout"]] += 1
                continue
            key = (card["set"], card["collector_number"])
            rank = _rank(card)
            entry = printings.get(key)
            if entry is None:
                entry = printings[key] = [None, None, set(), set()]
            entry[2].add(iso_lang(card["lang"]))
            entry[3].update(finish(f) for f in card.get("finishes") or [])
            if entry[0] is None or rank < entry[0]:
                entry[0] = rank
                entry[1] = (card["name"], card.get("rarity") or "", _image(card), card["id"])
    rows = []
    for (set_code, number), (_, (name, rarity, image, source_key), langs, finishes) in printings.items():
        rows.append(Row(
            id=row_id("mtg", set_code, number), kind="card", name=name, set_code=set_code, number=number,
            variant="", rarity=rarity, languages=tuple(sorted(langs)), finishes=tuple(sorted(finishes)),
            category="", image_source=image, source_key=source_key,
        ))
    return rows


def fetch(client: httpx.Client, cache_dir: Path) -> SourceData:
    try:
        sets, digital_sets = [], set()
        for s in _api(client, SETS_URL)["data"]:
            if s.get("digital"):
                digital_sets.add(s["code"])
                continue
            released = date.fromisoformat(s["released_at"]) if s.get("released_at") else None
            sets.append(CardSet(s["code"], s["name"], released))
        path = _bulk_file(client, cache_dir)
        skipped: Counter = Counter()
        try:
            rows = _rows(path, digital_sets, skipped)
        except Exception:
            path.unlink(missing_ok=True)  # a file that does not parse is downloaded again next time
            raise
    except (httpx.HTTPError, OSError, EOFError, StopIteration, ValueError, KeyError, TypeError,
            AttributeError, IndexError) as exc:
        raise SourceError(f"Scryfall: {type(exc).__name__}: {exc}") from exc
    if skipped:
        logger.info("Scryfall: skipped %s", ", ".join(f"{n} {why}" for why, n in sorted(skipped.items())))
    return SourceData(rows=rows, sets=sets)
