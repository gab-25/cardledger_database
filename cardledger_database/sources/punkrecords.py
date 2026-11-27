"""punk-records' One Piece card data (from the official Bandai card lists), English only: one row per printing, a
parallel (`OP01-001_p1`) being a row of its own. Read from `english/packs.json` and `english/index/cards_by_id.json`."""

import json
import logging
import re
from pathlib import Path

import httpx

from ..model import CardSet, Row, SourceData, SourceError, row_id

BASE_URL = "https://raw.githubusercontent.com/buhbbl/punk-records/main/{region}"
REGION = "english"  # punk-records folder

RARITIES = {
    "Common": "C", "Uncommon": "UC", "Rare": "R", "SuperRare": "SR", "SecretRare": "SEC", "Leader": "L",
    "Special": "SP", "TreasureRare": "TR", "Promo": "P",
}

_SIMPLE_LABEL = re.compile(r"^([A-Za-z]+)-(\d+)$")

logger = logging.getLogger(__name__)


def packs_url(region: str) -> str:
    return f"{BASE_URL.format(region=region)}/packs.json"


def cards_url(region: str) -> str:
    return f"{BASE_URL.format(region=region)}/index/cards_by_id.json"


def set_code(pack_id: str, label: str | None) -> str:
    """"OP-05" -> "op05", "OP14-EB04" -> "op14-eb04"; a pack without a label -> "pk<pack id>"."""
    label = (label or "").strip()
    if not label:
        return f"pk{pack_id}"
    m = _SIMPLE_LABEL.match(label)
    return (m.group(1) + m.group(2)).lower() if m else label.lower()


def _get_json(client: httpx.Client, url: str):
    response = client.get(url)
    response.raise_for_status()
    return response.json()


def _packs(client: httpx.Client, region: str, cache: Path) -> dict:
    """The region's packs.json; a non-empty one refreshes the cache, an empty or invalid one falls back to it."""
    try:
        packs = _get_json(client, packs_url(region))
    except ValueError:
        packs = None
    if isinstance(packs, dict) and packs:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(packs, ensure_ascii=False), encoding="utf-8")
        return packs
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = None
    cached = cached if isinstance(cached, dict) else {}
    logger.warning("punk-records %s packs.json is empty or invalid: using %s", region,
                   "the cached copy" if cached else "set codes as names (no cached copy)")
    return cached


def _pack_info(pack_id: str, pack: dict | None) -> tuple[str, str]:
    """(set code, set name) of a pack."""
    parts = (pack or {}).get("title_parts") or {}
    title = parts.get("title") or (pack or {}).get("raw_title") or ""
    code = set_code(pack_id, parts.get("label"))
    return code, title or code.upper()


def fetch(client: httpx.Client, cache_dir: Path) -> SourceData:
    rows: dict[str, Row] = {}
    try:
        packs = _packs(client, REGION, Path(cache_dir) / "punkrecords" / f"{REGION}-packs.json")
        cards = _get_json(client, cards_url(REGION))
        if not isinstance(cards, dict):
            raise TypeError(f"{REGION} cards_by_id.json is not an object")
        infos = {str(pid): _pack_info(str(pid), pack) for pid, pack in packs.items()}
        for key, c in cards.items():
            pack_id = str(c["pack_id"])
            if pack_id not in infos:
                infos[pack_id] = _pack_info(pack_id, None)
            code = infos[pack_id][0]
            number, _, variant = key.partition("_")
            variant = variant.lower()
            rows[row_id("onepiece", code, number, variant)] = Row(
                id=row_id("onepiece", code, number, variant), kind="card", name=c["name"], set_code=code,
                number=number, variant=variant, rarity=RARITIES.get(c.get("rarity") or "", c.get("rarity") or ""),
                languages=("en",), finishes=(), category="", image_source=(c.get("img_url") or "")[:500],
                source_key=f"{REGION}:{key}")
        sets: dict[str, CardSet] = {}
        for code, name in infos.values():
            sets.setdefault(code, CardSet(code, name, None))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"punk-records: {type(exc).__name__}: {exc}") from exc
    return SourceData(rows=list(rows.values()), sets=list(sets.values()))
