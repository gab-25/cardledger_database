import tomllib
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from cardledger_database.model import CmProduct, Enrichment, SourceCard, SourceSet

_PRODUCT_KEYS = {
    "id_product": int,
    "set_code": str,
    "collector_number": str,
    "rarity": str,
    "image_url": str,
    "image_large_url": str,
    "unmatched": bool,
}
_EXPANSION_KEYS = {"id_expansion": int, "code": str, "name": str, "released_at": date}
_CARD_FIELDS = ("set_code", "collector_number", "rarity", "image_url", "image_large_url")


@dataclass(frozen=True)
class Corrections:
    product: tuple[dict, ...] = ()
    expansion: tuple[dict, ...] = ()


def _check(entries, schema: dict, section: str, required: str, path: Path) -> tuple[dict, ...]:
    if not isinstance(entries, list):
        raise ValueError(f"{path}: [[{section}]] must be an array of tables")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: [[{section}]] must be an array of tables")
        if required not in entry:
            raise ValueError(f"{path}: [[{section}]] entry without {required}")
        for key, value in entry.items():
            if key not in schema:
                raise ValueError(f"{path}: unknown key {key!r} in [[{section}]]")
            expected = schema[key]
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                raise ValueError(f"{path}: {key!r} in [[{section}]] must be {expected.__name__}")
    return tuple(dict(e) for e in entries)


def load(slug: str, root: Path = Path("corrections")) -> Corrections:
    path = root / f"{slug}.toml"
    if not path.exists():
        return Corrections()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: invalid TOML: {exc}") from exc
    unknown = set(data) - {"product", "expansion"}
    if unknown:
        raise ValueError(f"{path}: unknown section {sorted(unknown)[0]!r}")
    return Corrections(
        _check(data.get("product", []), _PRODUCT_KEYS, "product", "id_product", path),
        _check(data.get("expansion", []), _EXPANSION_KEYS, "expansion", "id_expansion", path),
    )


def apply(
    corrections: Corrections, products: list[CmProduct], enrichment: Enrichment
) -> tuple[Enrichment, list[str]]:
    warnings: list[str] = []
    product_ids = {p.id_product for p in products}
    expansion_ids = {p.id_expansion for p in products}
    cards = dict(enrichment.cards)
    sets = dict(enrichment.sets)
    expansions = dict(enrichment.expansions)

    for entry in corrections.product:
        pid = entry["id_product"]
        if pid not in product_ids:
            warnings.append(f"correction for product {pid} skipped: not in the product list")
            continue
        if entry.get("unmatched"):
            cards.pop(pid, None)
            continue
        fields = {k: entry[k] for k in _CARD_FIELDS if k in entry}
        if pid in cards:
            cards[pid] = replace(cards[pid], **fields)
        elif "set_code" in fields:
            cards[pid] = SourceCard(**{**dict.fromkeys(_CARD_FIELDS, ""), **fields})
        else:
            warnings.append(f"correction for product {pid} skipped: not matched and no set_code given")

    for entry in corrections.expansion:
        eid = entry["id_expansion"]
        if eid not in expansion_ids:
            warnings.append(f"correction for expansion {eid} skipped: not in the product list")
            continue
        if "code" not in entry:
            warnings.append(f"correction for expansion {eid} skipped: no code given")
            continue
        code = entry["code"].lower()
        old = sets.get(code)
        sets[code] = SourceSet(
            code,
            entry.get("name", old.name if old else code.upper()),
            entry.get("released_at", old.released_at if old else None),
            old.language if old else "",
        )
        expansions[eid] = code

    return Enrichment(cards, sets, expansions), warnings
