from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    slug: str
    cm_id: int  # Cardmarket idGame
    name: str
    keep_unmatched: bool  # keep Cardmarket products the source has no card for


GAMES: dict[str, Game] = {
    g.slug: g
    for g in (
        Game("mtg", 1, "Magic: The Gathering", False),
        Game("pokemon", 6, "Pokémon", True),
        Game("lorcana", 19, "Disney Lorcana", True),
        Game("onepiece", 18, "One Piece Card Game", True),
        Game("yugioh", 3, "Yu-Gi-Oh!", True),
        Game("riftbound", 22, "Riftbound", True),
    )
}
