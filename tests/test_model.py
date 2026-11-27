from cardledger_database.model import GAMES, normalize_number, row_id, sealed_id, finish, slug


def test_games_registry():
    assert set(GAMES) == {"mtg", "pokemon", "lorcana", "onepiece", "yugioh", "riftbound"}
    assert {g.slug: g.source for g in GAMES.values()} == {
        "mtg": "scryfall", "pokemon": "tcgdex", "lorcana": "lorcanajson", "onepiece": "punkrecords",
        "yugioh": "ygoprodeck", "riftbound": "openrift"}
    assert all(g.confirm_only for g in GAMES.values())


def test_normalize_number():
    assert normalize_number("0187") == "187"
    assert normalize_number("012a") == "12A"  # upper case, as in core
    assert normalize_number("001a") == "1A"
    assert normalize_number(" 7 ") == "7"
    assert normalize_number("") == ""
    assert normalize_number(None) == ""


def test_row_id():
    assert row_id("mtg", "LEA", "161") == "mtg:lea:161"
    assert row_id("onepiece", "op01", "OP01-001", "p2") == "onepiece:op01:OP01-001:p2"
    assert sealed_id("pokemon", "sv01-booster-box") == "pokemon:sealed:sv01-booster-box"


def test_slug_and_finish():
    assert slug("Ultra Rare") == "ultra-rare" and slug(" Secret  Rare! ") == "secret-rare"
    assert slug("Pokémon Center") == "pokemon-center" and slug("ポケモン") == ""
    assert slug("Collector's Rare") == "collectors-rare" and slug("Pharaoh\u2019s Rare") == "pharaohs-rare"
    assert slug("Starlight Rare 'Alt'") == "starlight-rare-alt"
    assert finish("firstEdition") == "first_edition" and finish("Non Foil") == "non_foil"
