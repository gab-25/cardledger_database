from datetime import date

from cardledger_database.assemble import assemble
from cardledger_database.games import GAMES
from cardledger_database.model import CmProduct, Enrichment, SourceCard, SourceSet

SINGLES = {1}


def prod(id_product, id_expansion=10, id_category=1, name="Card", category_name="Magic Single"):
    return CmProduct(id_product, name, id_category, category_name, id_expansion, None)


def card(set_code, number="1", rarity="rare"):
    return SourceCard(set_code, number, rarity, "small", "large")


def enrich(cards=None, sets=None, expansions=None):
    return Enrichment(cards or {}, sets or {}, expansions or {})


def by_id(catalog):
    return {p.id_product: p for p in catalog.products}


def test_matched_product_takes_source_data():
    e = enrich({1: card("lea", "012a", "common")})
    c = assemble(GAMES["mtg"], [prod(1, name="Bolt")], e, SINGLES)
    p = c.products[0]
    assert (p.name, p.expansion_code, p.number, p.rarity) == ("Bolt", "lea", "12A", "common")
    assert (p.image_url, p.image_large_url, p.category_id, p.category_name) == ("small", "large", 1, "Magic Single")
    assert c.game == "mtg" and c.skipped == 0


def test_product_goes_to_the_main_set_of_its_expansion():
    e = enrich({1: card("lea", "1"), 2: card("lea", "2"), 3: card("leb", "3")})
    products = [prod(1), prod(2), prod(3), prod(9, id_category=5, name="Booster", category_name="Booster")]
    p = by_id(assemble(GAMES["mtg"], products, e, SINGLES))[9]
    assert (p.expansion_code, p.number, p.rarity, p.image_url, p.image_large_url) == ("lea", "", "", "", "")


def test_single_without_source_card_goes_to_the_main_set():
    e = enrich({1: card("lea", "1")})
    p = by_id(assemble(GAMES["mtg"], [prod(1), prod(2)], e, SINGLES))[2]
    assert (p.expansion_code, p.number) == ("lea", "")


def test_only_singles_vote_for_the_main_set():
    e = enrich({1: card("lea"), 2: card("tok"), 3: card("tok")})
    products = [prod(1), prod(2, id_category=5), prod(3, id_category=5), prod(9, id_category=5)]
    assert by_id(assemble(GAMES["mtg"], products, e, SINGLES))[9].expansion_code == "lea"


def test_orphan_product_is_skipped_and_counted():
    e = enrich({1: card("lea")})
    c = assemble(GAMES["mtg"], [prod(1), prod(2, id_expansion=99)], e, SINGLES)
    assert [p.id_product for p in c.products] == [1]
    assert c.skipped == 1


def test_orphan_product_gets_stand_in_expansion_when_kept():
    c = assemble(GAMES["yugioh"], [prod(2, id_expansion=77)], enrich(), SINGLES)
    assert c.skipped == 0
    assert c.products[0].expansion_code == "cmx77"
    (exp,) = c.expansions
    assert (exp.code, exp.name, exp.released_at) == ("cmx77", "Cardmarket expansion #77", None)


def test_known_expansion_mapping_wins_over_vote():
    e = enrich({1: card("lea"), 2: card("lea")}, expansions={10: "xyz"})
    c = assemble(GAMES["pokemon"], [prod(1), prod(2), prod(3)], e, SINGLES)
    assert by_id(c)[3].expansion_code == "xyz"


def test_main_set_tie_prefers_shorter_code():
    e = enrich({1: card("abcd"), 2: card("ab"), 3: card("abc")})
    c = assemble(GAMES["mtg"], [prod(1), prod(2), prod(3), prod(4, id_category=5)], e, SINGLES)
    assert by_id(c)[4].expansion_code == "ab"


def test_main_set_tie_same_length_is_alphabetical():
    e = enrich({1: card("bb"), 2: card("aa")})
    c = assemble(GAMES["mtg"], [prod(1), prod(2), prod(4, id_category=5)], e, SINGLES)
    assert by_id(c)[4].expansion_code == "aa"


def test_expansions_are_those_used_with_name_and_date_from_sets():
    sets = {"lea": SourceSet("lea", "Alpha", date(1993, 8, 5)), "unused": SourceSet("unused", "U", None)}
    e = enrich({1: card("lea"), 2: card("zzz")}, sets=sets)
    c = assemble(GAMES["mtg"], [prod(1), prod(2)], e, SINGLES)
    got = {x.code: x for x in c.expansions}
    assert set(got) == {"lea", "zzz"}
    assert (got["lea"].name, got["lea"].released_at) == ("Alpha", date(1993, 8, 5))
    assert (got["zzz"].name, got["zzz"].released_at) == ("ZZZ", None)


def test_input_order_is_kept_and_fields_truncated():
    e = enrich({1: card("lea", "9" * 50, "r" * 50)})
    e.cards[1] = SourceCard("lea", "9" * 50, "r" * 50, "i" * 600, "j" * 600)
    c = assemble(GAMES["mtg"], [prod(2, name="n" * 300), prod(1, name="n" * 300)], e, SINGLES)
    assert [p.id_product for p in c.products] == [2, 1]
    p = by_id(c)[1]
    assert len(p.name) == 200 and len(p.number) == 40 and len(p.rarity) == 40
    assert len(p.image_url) == 500 and len(p.image_large_url) == 500


def test_unmatched_product_takes_the_code_in_its_name_as_number():
    e = enrich({1: card("op13", "OP13-001")}, expansions={10: "op13"})
    products = [prod(1, name="Luffy (OP13-001)"), prod(2, name="Sabo (OP13-004)"), prod(3, name="Sabo")]
    c = by_id(assemble(GAMES["onepiece"], products, e, SINGLES))
    assert (c[2].expansion_code, c[2].number) == ("op13", "OP13-004")
    assert c[3].number == ""


def test_stand_in_product_takes_the_code_in_its_name_as_number():
    c = assemble(GAMES["onepiece"], [prod(2, id_expansion=77, name="Sabo (OP13-004) (Parallel)")], enrich(), SINGLES)
    assert (c.products[0].expansion_code, c.products[0].number) == ("cmx77", "OP13-004")
