import re
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class CmProduct:
    id_product: int
    name: str
    id_category: int
    category_name: str
    id_expansion: int
    id_metacard: int | None


@dataclass(frozen=True)
class SourceCard:
    set_code: str
    collector_number: str
    rarity: str
    image_url: str
    image_large_url: str


@dataclass(frozen=True)
class SourceSet:
    code: str
    name: str
    released_at: date | None
    language: str = ""  # "ja" for Japanese Pokémon sets, otherwise ""


@dataclass(frozen=True)
class Enrichment:
    cards: dict[int, SourceCard]
    sets: dict[str, SourceSet]
    expansions: dict[int, str] = field(default_factory=dict)  # Cardmarket idExpansion -> set code


@dataclass(frozen=True)
class Product:
    id_product: int
    name: str
    expansion_code: str
    number: str
    rarity: str
    image_url: str
    image_large_url: str
    category_id: int
    category_name: str


@dataclass(frozen=True)
class GameCatalog:
    game: str
    expansions: list[SourceSet]
    products: list[Product]
    skipped: int


_NUMBER = re.compile(r"^0*(\d+)(.*)$")
CODE = re.compile(r"\(([A-Z0-9]{1,6}-(?:[A-Z]{2})?\d{1,4}[A-Z]?)\)")


def code_of(name: str) -> str | None:
    """The card code printed in a Cardmarket name ("Luffy (OP01-001)" -> "OP01-001"), if any."""
    m = CODE.search(name)
    return m.group(1) if m else None


def normalize_number(value: str | None) -> str:
    """Canonical collector number: no spaces, no leading zeros, upper case ("012a" -> "12A")."""
    value = (value or "").strip().replace(" ", "").upper()
    m = _NUMBER.match(value)
    if m:
        return (m.group(1) or "0") + m.group(2)
    return value
