from cardledger_database.model import GAMES, normalize_number


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


def test_cardmarket_id_accepts_only_integers():
    from cardledger_database.model import cardmarket_id
    assert [cardmarket_id(v) for v in (5, "5", " 5 ", 5.0)] == [5, 5, 5, 5]
    assert [cardmarket_id(v) for v in (None, "", "abc", "1.5", 1.5, True, [1])] == [None] * 7
