"""Scryfall's default cards bulk file for Magic: one row per printing (English, or the printed language of cards
printed in one language only). Not openly licensed: Scryfall asks for its bulk files instead of per-card calls and
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

logger = logging.getLogger(__name__)


def _api(client: httpx.Client, url: str) -> dict:
    response = http.get(client, url, headers=API_HEADERS)
    response.raise_for_status()
    return response.json()


def _bulk_file(client: httpx.Client, cache_dir: Path) -> Path:
    """The default cards file, downloaded unless the cached copy has the index's `updated_at`."""
    entry = next(d for d in _api(client, BULK_URL)["data"] if d["type"] == "default_cards")
    url = entry["jsonl_download_uri"]
    folder = Path(cache_dir) / "scryfall"
    path = folder / f"default_cards-{re.sub(r'[^0-9A-Za-z.-]', '-', entry['updated_at'])}.jsonl.gz"
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
    for old in folder.glob("default_cards-*"):
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


def _rows(path: Path, digital_sets: set[str], skipped: Counter) -> list[Row]:
    rows = []
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
            rows.append(Row(
                id=row_id("mtg", card["set"], card["collector_number"]), kind="card", name=card["name"],
                set_code=card["set"], number=card["collector_number"], variant="", rarity=card.get("rarity") or "",
                languages=(card["lang"],), finishes=tuple(sorted(finish(f) for f in card.get("finishes") or [])),
                category="", image_source=_image(card), source_key=card["id"],
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
