import gzip
import hashlib
import json
from datetime import date, datetime

from cardledger_database.model import GameCatalog, Product, SourceSet

FORMAT_VERSION = 1
MANIFEST_KEY = f"catalog/v{FORMAT_VERSION}/manifest.json"


def catalog_key(slug: str) -> str:
    return f"catalog/v{FORMAT_VERSION}/{slug}.jsonl.gz"


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def encode(catalog: GameCatalog) -> bytes:
    lines = []
    for e in sorted(catalog.expansions, key=lambda e: e.code):
        lines.append(_line({
            "type": "expansion",
            "code": e.code,
            "name": e.name,
            "released_at": e.released_at.isoformat() if e.released_at else None,
            "language": e.language,
        }))
    for p in sorted(catalog.products, key=lambda p: (p.expansion_code, p.id_product)):
        lines.append(_line({
            "type": "product",
            "id_product": p.id_product,
            "name": p.name,
            "expansion_code": p.expansion_code,
            "number": p.number,
            "rarity": p.rarity,
            "image_url": p.image_url,
            "image_large_url": p.image_large_url,
            "category_id": p.category_id,
            "category_name": p.category_name,
        }))
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    return gzip.compress(raw, mtime=0)


def decode(data: bytes, game: str) -> GameCatalog:
    expansions: list[SourceSet] = []
    products: list[Product] = []
    for line in gzip.decompress(data).decode("utf-8").splitlines():
        if not line:
            continue
        d = json.loads(line)
        kind = d.pop("type")
        if kind == "expansion":
            released = d["released_at"]
            expansions.append(SourceSet(
                d["code"], d["name"], date.fromisoformat(released) if released else None, d["language"]
            ))
        elif kind == "product":
            products.append(Product(**d))
    return GameCatalog(game, expansions, products, 0)


def game_entry(slug: str, data: bytes, catalog: GameCatalog, generated_at: datetime) -> dict:
    return {
        "key": catalog_key(slug),
        "sha256": hashlib.sha256(data).hexdigest(),
        "products": len(catalog.products),
        "enriched": catalog.enriched,
        "expansions": len(catalog.expansions),
        "generated_at": generated_at.isoformat(),
    }


def encode_manifest(games: dict[str, dict], generated_at: datetime) -> bytes:
    manifest = {"format_version": FORMAT_VERSION, "generated_at": generated_at.isoformat(), "games": games}
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def read_manifest(data: bytes | None) -> dict:
    if data is None:
        return {"format_version": FORMAT_VERSION, "generated_at": None, "games": {}}
    manifest = json.loads(data.decode("utf-8"))
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"unsupported manifest format_version: {manifest.get('format_version')!r}")
    return manifest
