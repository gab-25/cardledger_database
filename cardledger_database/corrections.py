import tomllib
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from cardledger_database.model import CardSet, SourceData, row_id

_CARD_KEYS = {"id": str, "name": str, "number": str, "rarity": str, "image_source": str, "drop": bool}
_SET_KEYS = {"code": str, "name": str, "released_at": date, "source_code": str}
_SECTIONS = ("card", "set")
_CARD_FIELDS = ("name", "number", "rarity", "image_source")


@dataclass(frozen=True)
class Corrections:
    card: tuple[dict, ...] = ()
    set: tuple[dict, ...] = ()


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


def _read(path: Path, section: str, schema: dict, required: str, allowed=None) -> tuple[dict, ...]:
    """The checked entries of a file's section (`allowed` sections may coexist); a missing file has none."""
    if not path.exists():
        return ()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: invalid TOML: {exc}") from exc
    unknown = set(data) - set(allowed or (section,))
    if unknown:
        raise ValueError(f"{path}: unknown section {sorted(unknown)[0]!r}")
    return _check(data.get(section, []), schema, section, required, path)


def load(slug: str, root: Path = Path("corrections")) -> Corrections:
    """The fixes of corrections/<slug>.toml: [[card]] by catalog id and [[set]] by set code."""
    path = root / f"{slug}.toml"
    return Corrections(_read(path, "card", _CARD_KEYS, "id", _SECTIONS), _read(path, "set", _SET_KEYS, "code", _SECTIONS))


def apply(game: str, corrections: Corrections, data: SourceData) -> tuple[SourceData, list[str]]:
    warnings: list[str] = []
    rows = list(data.rows)
    sets = list(data.sets)

    for entry in corrections.set:
        code = entry["code"]
        source = entry.get("source_code")
        target = source if source is not None else code
        if not any(s.code == target for s in sets):
            warnings.append(f"correction for set {target} skipped: not in the source")
            continue
        if source is not None:
            rows = [
                replace(r, set_code=code, id=row_id(game, code, r.number, r.variant)) if r.set_code == source else r
                for r in rows
            ]
        sets = [
            replace(
                s,
                code=code,
                **{k: entry[k] for k in ("name", "released_at") if k in entry},
            )
            if s.code == target
            else s
            for s in sets
        ]

    for entry in corrections.card:
        rid = entry["id"]
        if not any(r.id == rid for r in rows):
            warnings.append(f"correction for card {rid} skipped: not in the source")
            continue
        if entry.get("drop"):
            rows = [r for r in rows if r.id != rid]
            continue
        fields = {k: entry[k] for k in _CARD_FIELDS if k in entry}
        rows = [replace(r, **fields) if r.id == rid else r for r in rows]

    return SourceData(rows, sets), warnings
