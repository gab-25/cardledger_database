from datetime import date

from cardledger_database import matcher
from cardledger_database.matcher import MatchResult, card_key, match, normalize_name
from cardledger_database.model import CmProduct, SourceSet
from cardledger_database.sources.tcgcsv import TcgGroup, TcgProduct

SINGLES = {1}


def cm(id_product, name, id_expansion=10, id_category=1, id_metacard=None):
    return CmProduct(id_product, name, id_category, "Single" if id_category == 1 else "Booster", id_expansion,
                     id_metacard)


def tcg(product_id, name, number, group_id=100, rarity="Common"):
    return TcgProduct(product_id, group_id, name, number, rarity, f"https://tcgplayer-cdn/{product_id}_200w.jpg")


def group(group_id=100, abbreviation="OP01", name="Romance Dawn"):
    return TcgGroup(group_id, name, abbreviation, date(2022, 12, 2))


NAMES = ["Alpha Dragon", "Beta Knight", "Gamma Wizard", "Delta Rogue"]


def named_set(id_expansion=10, group_id=100, first_id=1, first_pid=1000):
    """Four uniquely named singles on both sides."""
    products = [cm(first_id + i, n, id_expansion) for i, n in enumerate(NAMES)]
    cards = [tcg(first_pid + i, n, str(i + 1), group_id) for i, n in enumerate(NAMES)]
    return products, cards


# --- keys and normalization ---

def test_card_key_prefers_the_code():
    assert card_key("Monkey.D.Luffy (OP01-001) (V.2)") == ("code", "OP01-001")
    assert card_key("Blue-Eyes White Dragon (LOB-EN001)") == ("code", "LOB-EN001")
    assert card_key("Jinx, Loose Cannon") == ("name", "jinx loose cannon")


def test_normalize_name():
    assert normalize_name("Pokémon Trainer [Alt Art] (V.1) - Foil") == "pokemon trainer"
    assert normalize_name("Fire/Ice") == "fire/ice"
    assert normalize_name("  Dark  Magician!! ") == "dark magician"


# --- matching ---

def test_unique_name_is_matched():
    products, cards = named_set()
    result = match(products, [group()], cards, SINGLES)
    assert isinstance(result, MatchResult)
    assert result.card_pairs == {1: 1000, 2: 1001, 3: 1002, 4: 1003}
    assert result.expansion_pairs == {10: 100}
    c = result.enrichment.cards[2]
    assert (c.set_code, c.collector_number, c.rarity) == ("op01", "2", "Common")
    assert result.enrichment.expansions == {10: "op01"}
    assert result.enrichment.sets == {"op01": SourceSet("op01", "Romance Dawn", date(2022, 12, 2))}


def test_code_in_cardmarket_name_is_used():
    products = [cm(1, "Luffy (OP01-001)"), cm(2, "Zoro (OP01-025)"), cm(3, "Nami (OP01-016)")]
    cards = [tcg(1000, "Monkey.D.Luffy", "OP01-001"), tcg(1001, "Roronoa Zoro", "op01-025"),
             tcg(1002, "Nami", "OP01-016")]
    result = match(products, [group()], cards, SINGLES)
    assert result.card_pairs == {1: 1000, 2: 1001, 3: 1002}
    assert result.enrichment.cards[2].collector_number == "OP01-025"


def test_duplicate_name_on_one_side_is_not_matched():
    products, cards = named_set()
    products.append(cm(5, "Alpha Dragon"))  # Cardmarket twin
    cards.append(tcg(1004, "Beta Knight", "9"))  # TCGplayer twin
    result = match(products, [group()], cards, SINGLES)
    assert result.card_pairs == {3: 1002, 4: 1003}


def test_same_key_with_different_metacards_is_not_matched():
    products, cards = named_set()
    products[0] = cm(1, "Alpha Dragon", id_metacard=7)
    products.append(cm(5, "Alpha Dragon", id_metacard=8))
    result = match(products, [group()], cards, SINGLES)
    assert 1 not in result.card_pairs and 5 not in result.card_pairs


def test_variants_are_not_paired_by_id_order():
    products = [cm(1, "Luffy (OP01-001)"), cm(2, "Luffy (OP01-001) (V.2)"), cm(3, "Zoro (OP01-025)"),
                cm(4, "Nami (OP01-016)")]
    cards = [tcg(1000, "Monkey.D.Luffy", "OP01-001"), tcg(1001, "Monkey.D.Luffy (Parallel)", "OP01-001"),
             tcg(1002, "Roronoa Zoro", "OP01-025"), tcg(1003, "Nami", "OP01-016")]
    result = match(products, [group()], cards, SINGLES)
    assert result.card_pairs == {3: 1002, 4: 1003}
    assert 1 not in result.enrichment.cards and 2 not in result.enrichment.cards


def test_expansion_below_threshold_is_not_matched():
    products = [cm(i + 1, f"Card {chr(97 + i)}") for i in range(10)]
    # only 2 of 10 keys in common: 20% < 30%
    cards = [tcg(1000, "Card a", "1"), tcg(1001, "Card b", "2")] + [
        tcg(1002 + i, f"Other {i}", str(3 + i)) for i in range(3)]
    result = match(products, [group()], cards, SINGLES)
    assert result.expansion_pairs == {}
    assert result.card_pairs == {}
    assert result.enrichment.expansions == {} and result.enrichment.sets == {}


def test_single_common_key_is_not_enough():
    products = [cm(1, "Alpha Dragon"), cm(2, "Beta Knight")]
    cards = [tcg(1000, "Alpha Dragon", "1")]
    assert match(products, [group()], cards, SINGLES).expansion_pairs == {}


def test_ambiguous_expansion_is_not_matched():
    products, cards = named_set()
    _, reprint = named_set(group_id=200, first_pid=2000)
    result = match(products, [group(), group(200, "RP01", "Reprints")], cards + reprint, SINGLES)
    assert result.expansion_pairs == {}
    assert result.card_pairs == {}


def test_best_group_wins_with_a_clear_margin():
    products, cards = named_set()
    partial = [tcg(2000, "Alpha Dragon", "1", 200), tcg(2001, "Beta Knight", "2", 200)] + [
        tcg(2002 + i, f"Filler {i}", str(10 + i), 200) for i in range(6)]
    result = match(products, [group(), group(200, "RP01", "Reprints")], cards + partial, SINGLES)
    assert result.expansion_pairs == {10: 100}


def test_unmatched_card_still_gets_expansion():
    products, cards = named_set()
    products.append(cm(5, "Epsilon Golem"))  # no TCGplayer card
    products.append(cm(6, "Booster Box", id_category=5))  # not a single
    cards.append(tcg(1004, "Booster Box", "99"))
    result = match(products, [group()], cards, SINGLES)
    assert 5 not in result.enrichment.cards and 6 not in result.enrichment.cards
    assert 6 not in result.card_pairs
    assert result.enrichment.expansions == {10: "op01"}


def test_large_image_url():
    products, cards = named_set()
    c = match(products, [group()], cards, SINGLES).enrichment.cards[1]
    assert c.image_url == "https://tcgplayer-cdn/1000_200w.jpg"
    assert c.image_large_url == "https://tcgplayer-cdn/1000_in_1000x1000.jpg"


def test_set_code_falls_back_to_group_id():
    products, cards = named_set()
    result = match(products, [group(abbreviation="")], cards, SINGLES)
    assert result.enrichment.expansions == {10: "tcg100"}
    assert result.enrichment.cards[1].set_code == "tcg100"


def test_shared_abbreviation_falls_back_to_group_id():
    products, cards = named_set()
    other = group(200, "OP01", "Romance Dawn (Japanese)")
    result = match(products, [group(), other], cards, SINGLES)
    assert result.enrichment.expansions == {10: "tcg100"}


def test_thresholds_are_module_constants():
    assert (matcher.MIN_COMMON, matcher.MIN_SHARE, matcher.MIN_MARGIN) == (2, 0.3, 0.1)


def test_result_lists_the_expansion_of_every_single():
    products, cards = named_set()
    products += [cm(5, "Lonely Card", 11), cm(6, "Booster Box", 11, id_category=5)]
    result = match(products, [group()], cards, SINGLES)
    assert result.single_expansions == {1: 10, 2: 10, 3: 10, 4: 10, 5: 11}


# --- one expansion per group (Magic bench) ---

def test_group_goes_only_to_its_best_expansion():
    products, cards = named_set()
    # a second expansion with the same four names and two more (e.g. a promo set of the same cards)
    promos = [cm(11 + i, n, 11) for i, n in enumerate(NAMES + ["Omega Titan", "Sigma Seer"])]
    result = match(products + promos, [group()], cards, SINGLES)
    assert result.expansion_pairs == {10: 100}
    assert set(result.card_pairs) == {1, 2, 3, 4}


def test_expansions_tied_for_a_group_get_none():
    products, cards = named_set()
    twins, _ = named_set(id_expansion=11, first_id=11)
    result = match(products + twins, [group()], cards, SINGLES)
    assert result.expansion_pairs == {}
    assert result.card_pairs == {}


def test_expansion_with_more_printings_than_its_group_is_not_matched():
    # every name twice on Cardmarket (e.g. prerelease and promo pack stamps), once in the group
    products = [cm(i + 1, f"{n} (V.{v})") for i, (n, v) in enumerate((n, v) for n in NAMES for v in (1, 2))]
    _, cards = named_set()
    assert match(products, [group()], cards, SINGLES).expansion_pairs == {}


def test_expansion_with_some_reprinted_names_is_matched():
    products, cards = named_set()
    products += [cm(5, "Alpha Dragon (V.2)"), cm(6, "Beta Knight (V.2)")]  # 6 printings of 4 names: ratio 1.5
    result = match(products, [group()], cards, SINGLES)
    assert result.expansion_pairs == {10: 100}
    assert matcher.MAX_PRINTING_RATIO == 1.5
