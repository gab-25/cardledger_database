"""The data model: the games, the Cardmarket products, what a source adds to them and the assembled catalog."""

import re
from dataclasses import dataclass, field
from datetime import date


class SourceError(Exception):
    """A public data source could not be downloaded or did not have the expected shape."""


@dataclass(frozen=True)
class Game:
    slug: str
    cm_id: int  # Cardmarket idGame
    name: str
    keep_unmatched: bool  # keep Cardmarket products the source has no card for
    source: str  # mtgjson, tcgdex, lorcanajson or tcgcsv
    tcgcsv_category: int | None = None


GAMES: dict[str, Game] = {
    g.slug: g
    for g in (
        Game("mtg", 1, "Magic: The Gathering", False, "mtgjson"),
        Game("pokemon", 6, "Pokémon", True, "tcgdex"),
        Game("lorcana", 19, "Disney Lorcana", True, "lorcanajson"),
        Game("onepiece", 18, "One Piece Card Game", True, "tcgcsv", 68),
        Game("yugioh", 3, "Yu-Gi-Oh!", True, "tcgcsv", 2),
        Game("riftbound", 22, "Riftbound", True, "tcgcsv", 89),
    )
}


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
    enriched: int = 0  # products that got a source card (their id is in the corrected enrichment's cards)


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


def cardmarket_id(value) -> int | None:
    """A Cardmarket id from a source as an int (also from "123" or 123.0); None when missing or not an integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
