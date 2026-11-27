"""The data model: the games, the printings a source returns and the assembled catalog."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date


class SourceError(Exception):
    """A public data source could not be downloaded or did not have the expected shape."""


@dataclass(frozen=True)
class Game:
    slug: str
    name: str
    source: str  # scryfall, tcgdex, lorcanajson, punkrecords, ygoprodeck or openrift
    # The game's recognition index never accepts a card without confirmation. Every game is confirm-only for now: set
    # it to False for a game once cardledger_vision's bench finds at most 2% wrong_certain for it.
    confirm_only: bool = True


GAMES: dict[str, Game] = {
    g.slug: g
    for g in (
        Game("mtg", "Magic: The Gathering", "scryfall"),
        Game("pokemon", "Pokémon", "tcgdex"),
        Game("lorcana", "Disney Lorcana", "lorcanajson"),
        Game("onepiece", "One Piece Card Game", "punkrecords"),
        Game("yugioh", "Yu-Gi-Oh!", "ygoprodeck"),
        Game("riftbound", "Riftbound", "openrift"),
    )
}


@dataclass(frozen=True)
class CardSet:
    code: str
    name: str
    released_at: date | None
    region: str = ""  # "ja" for Japanese sets, otherwise ""


@dataclass(frozen=True)
class Row:
    """One printing (or sealed product) of the game's own dataset."""
    id: str
    kind: str
    name: str
    set_code: str
    number: str
    variant: str
    rarity: str
    languages: tuple[str, ...]
    finishes: tuple[str, ...]
    category: str
    image_source: str = ""  # source URL of the largest image; not written
    image: str = ""  # bucket-relative path, filled by images.host
    image_large: str = ""
    source_key: str = ""  # the source's own key for the row; orders merges; not written


@dataclass(frozen=True)
class SourceData:
    rows: list[Row]
    sets: list[CardSet]


@dataclass(frozen=True)
class GameCatalog:
    game: str
    sets: list[CardSet]
    rows: list[Row]
    merged: int = 0  # rows folded into another with the same id


def row_id(game: str, set_code: str, number: str, variant: str = "") -> str:
    """The id of a printing: "mtg:lea:161", with the variant appended when there is one."""
    parts = [game, set_code.lower(), number]
    if variant:
        parts.append(variant)
    return ":".join(parts)


def sealed_id(game: str, key: str) -> str:
    return f"{game}:sealed:{key}"


def slug(value: str) -> str:
    """Accents folded, lower case, runs of anything but [a-z0-9] become "-", trimmed: "Ultra Rare" -> "ultra-rare",
    "Pokémon Center" -> "pokemon-center". What does not fold to ASCII (Japanese) is dropped."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


def finish(value: str) -> str:
    """Lower case with "_" between words: "firstEdition" -> "first_edition", "Non Foil" -> "non_foil"."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value.strip())
    return re.sub(r"[\s_-]+", "_", spaced).lower()


_NUMBER = re.compile(r"^0*(\d+)(.*)$")


def normalize_number(value: str | None) -> str:
    """Canonical collector number: no spaces, no leading zeros, upper case ("012a" -> "12A")."""
    value = (value or "").strip().replace(" ", "").upper()
    m = _NUMBER.match(value)
    if m:
        return (m.group(1) or "0") + m.group(2)
    return value
