from cardledger_database.games import GAMES
from cardledger_database.model import normalize_number


def test_games_registry():
    assert set(GAMES) == {"mtg", "pokemon", "lorcana", "onepiece", "yugioh", "riftbound"}
    assert GAMES["pokemon"].cm_id == 6
    assert GAMES["mtg"].keep_unmatched is False
    assert GAMES["yugioh"].keep_unmatched is True


def test_normalize_number():
    assert normalize_number("0187") == "187"
    assert normalize_number("012a") == "12A"  # upper case, as in core
    assert normalize_number("001a") == "1A"
    assert normalize_number(" 7 ") == "7"
    assert normalize_number("") == ""
    assert normalize_number(None) == ""
