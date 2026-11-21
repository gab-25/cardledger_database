import importlib.util
from pathlib import Path

from cardledger_database.matcher import MatchResult
from cardledger_database.model import Enrichment
from cardledger_database.matcher import Printing

SPEC = importlib.util.spec_from_file_location("run_bench", Path(__file__).parent.parent / "scripts" / "run_bench.py")
run_bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_bench)
BenchResult, bench = run_bench.BenchResult, run_bench.bench


def tcg(product_id, group_id):
    return Printing(product_id, group_id, f"Card {product_id}", str(product_id), "C", "", "")


def result(card_pairs, expansion_pairs, single_expansions):
    return MatchResult(Enrichment({}, {}), card_pairs, expansion_pairs, single_expansions)


# group 100: products 1000-1003; group 200: products 2000-2003
TCG = [tcg(1000 + i, 100) for i in range(4)] + [tcg(2000 + i, 200) for i in range(4)]


def test_bench_counts_card_errors():
    truth = {1: {1000}, 2: {1001, 5001}, 3: {1002}}
    # 1 and 2 right (2 by its second TCGplayer id), 3 paired with another card
    r = bench(result({1: 1000, 2: 5001, 3: 1003}, {}, {1: 10, 2: 10, 3: 10}), truth, TCG)
    assert (r.card_matches, r.card_errors) == (3, 1)
    assert r.card_error_rate == 1 / 3


def test_bench_counts_expansion_errors():
    truth = {1: {1000}, 2: {1001}, 3: {2000}, 4: {2001}, 5: {2002}, 6: {1003}}
    singles = {1: 10, 2: 10, 3: 10, 4: 20, 5: 20, 6: 20}
    # expansion 10: two true cards in group 100, one in 200 -> 100 is right
    # expansion 20: two in 200, one in 100 -> 100 is wrong
    r = bench(result({}, {10: 100, 20: 100}, singles), truth, TCG)
    assert (r.expansion_matches, r.expansion_errors) == (2, 1)
    assert r.expansion_error_rate == 0.5
    assert (r.card_matches, r.card_errors, r.card_error_rate) == (0, 0, 0)


def test_bench_fails_above_one_percent():
    assert run_bench.MAX_ERROR_RATE == 0.01
    assert BenchResult(100, 1, 100, 1).passed
    assert not BenchResult(100, 2, 100, 0).passed
    assert not BenchResult(100, 0, 100, 2).passed
    assert BenchResult(0, 0, 0, 0).passed
    assert (BenchResult(0, 0, 0, 0).card_error_rate, BenchResult(0, 0, 0, 0).expansion_error_rate) == (0, 0)


def test_bench_ignores_products_without_truth():
    truth = {1: {1000}, 3: {9999}}  # 9999 is in no TCGCSV group
    singles = {1: 10, 2: 10, 3: 20, 4: 30}
    r = bench(result({1: 1000, 2: 2000, 4: 2001}, {10: 100, 20: 200, 30: 200}, singles), truth, TCG)
    # cards 2 and 4 have no truth; expansion 20 has no true card in a group, 30 none in the truth
    assert (r.card_matches, r.card_errors) == (1, 0)
    assert (r.expansion_matches, r.expansion_errors) == (1, 0)


def test_reused_product_ids():
    r = result({1: 1000, 2: 1000, 3: 1001, 4: 1000, 5: 1002, 6: 1002}, {}, {})
    assert run_bench.reused_product_ids(r) == {1000: [1, 2, 4], 1002: [5, 6]}
