"""A game's catalog: the printings of a source plus the sealed products, merged by id, written as one JSON document."""

import json
from collections import defaultdict
from dataclasses import fields, replace

from .model import CardSet, GameCatalog, Row, SourceData

FORMAT_VERSION = 1
_CAPS = {"name": 200, "number": 40, "variant": 40, "rarity": 40, "category": 40}
_NOT_COMPARED = {"languages", "finishes", "source_key"}


def catalog_key(slug: str) -> str:
    """The path of a game's file, the same under the output folder and in the bucket."""
    return f"catalog/v{FORMAT_VERSION}/{slug}.json"


def _merge(rows: list[Row]) -> tuple[Row, bool]:
    """One row from rows sharing an id: the first by source_key, with the languages and finishes of all. The flag
    says whether the rows differed in anything else."""
    rows = sorted(rows, key=lambda r: r.source_key)
    first = rows[0]
    differ = any(getattr(r, f.name) != getattr(first, f.name)
                 for r in rows[1:] for f in fields(Row) if f.name not in _NOT_COMPARED)
    merged = replace(first,
                     languages=tuple(sorted({v for r in rows for v in r.languages})),
                     finishes=tuple(sorted({v for r in rows for v in r.finishes})))
    return merged, differ


def assemble(game: str, data: SourceData, sealed: list[Row]) -> tuple[GameCatalog, list[str]]:
    """The catalog and its warnings: rows with the same id are merged, sets no row uses are dropped and sets rows use
    but the source lacks are made up from their code."""
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in [*data.rows, *sealed]:
        groups[row.id].append(row)
    rows: list[Row] = []
    warnings: list[str] = []
    merged = 0
    for id_, group in sorted(groups.items()):
        row, differ = _merge(group)
        rows.append(row)
        merged += len(group) - 1
        if differ:
            warnings.append(f"{game}: {len(group)} rows share the id {id_} and differ in more than languages/finishes")
    known = {s.code: s for s in data.sets}
    sets = [known.get(code) or CardSet(code, code.upper(), None) for code in sorted({r.set_code for r in rows})]
    return GameCatalog(game, sets, rows, merged), warnings


def encode(catalog: GameCatalog) -> bytes:
    """One JSON object, {"rows": [...], "sets": [...]}, both sorted; the same catalog always gives the same bytes."""
    sets = [{
        "code": s.code,
        "name": s.name,
        "released_at": s.released_at.isoformat() if s.released_at else None,
        "region": s.region,
    } for s in sorted(catalog.sets, key=lambda s: s.code)]
    rows = [{
        "id": r.id,
        "kind": r.kind,
        "name": r.name[:_CAPS["name"]],
        "set_code": r.set_code,
        "number": r.number[:_CAPS["number"]],
        "variant": r.variant[:_CAPS["variant"]],
        "rarity": r.rarity[:_CAPS["rarity"]],
        "languages": sorted(r.languages),
        "finishes": sorted(r.finishes),
        "category": r.category[:_CAPS["category"]],
        "image": r.image,
        "image_large": r.image_large,
    } for r in sorted(catalog.rows, key=lambda r: r.id)]
    doc = {"rows": rows, "sets": sets}
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
