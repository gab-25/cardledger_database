import importlib.util
from pathlib import Path

from cardledger_database.matcher import MatchResult
from cardledger_database.model import Enrichment, SourceCard

SPEC = importlib.util.spec_from_file_location("run_bench", Path(__file__).parent.parent / "scripts" / "run_bench.py")
run_bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_bench)
BenchResult, bench = run_bench.BenchResult, run_bench.bench


def card(set_code, number):
    return SourceCard(set_code, number, "", "", "")


def result(cards, card_pairs=(), expansions=None, single_expansions=None):
    """cards: {idProduct: (set, number)}; card_pairs: the ids that are pairs (the others are fill-ins)."""
    enrichment = Enrichment({pid: card(*sn) for pid, sn in cards.items()}, {}, expansions or {})
    pairs = {pid: 9000 + pid for pid in card_pairs}
    exp_pairs = {exp: 1 for exp in (expansions or {})}
    return MatchResult(enrichment, pairs, exp_pairs, single_expansions or {})


TRUTH = Enrichment({1: card("dmu", "1"), 2: card("dmu", "2"), 3: card("dmu", "3"), 4: card("neo", "4"),
                    5: card("neo", "5"), 6: card("dmu", "6")}, {})


def test_bench_counts_pair_and_fill_in_errors():
    # 1 right; 2 wrong number ("02" normalizes to "2": right); 3 wrong set; 4 fill-in right; 5 fill-in wrong
    r = bench(result({1: ("dmu", "1"), 2: ("dmu", "02"), 3: ("neo", "3"), 4: ("neo", "4"), 5: ("neo", "6")},
                     card_pairs=(1, 2, 3)), TRUTH)
    assert (r.card_matches, r.card_errors, r.fill_matches, r.fill_errors) == (3, 1, 2, 1)
    assert r.card_error_rate == 2 / 5


def test_bench_counts_expansion_errors():
    singles = {1: 10, 2: 10, 4: 10, 3: 20, 5: 20, 6: 20}
    # expansion 10: dmu, dmu, neo -> dmu right; expansion 20: dmu, neo, dmu -> neo wrong
    r = bench(result({}, expansions={10: "dmu", 20: "neo"}, single_expansions=singles), TRUTH)
    assert (r.expansion_matches, r.expansion_errors) == (2, 1)
    assert r.expansion_error_rate == 0.5


def test_bench_ignores_products_without_truth():
    r = bench(result({7: ("dmu", "1"), 1: ("dmu", "1")}, card_pairs=(7, 1), expansions={30: "dmu"},
                     single_expansions={7: 30}), TRUTH)
    assert (r.card_matches, r.card_errors) == (1, 0)
    assert (r.expansion_matches, r.expansion_errors) == (0, 0)


def test_bench_fails_above_one_percent():
    assert run_bench.MAX_ERROR_RATE == 0.01
    assert BenchResult(100, 1, 0, 0, 100, 1).passed
    assert not BenchResult(100, 1, 100, 2, 100, 0).passed  # 3 / 200 cards
    assert not BenchResult(100, 0, 0, 0, 100, 2).passed
    assert BenchResult(0, 0, 0, 0, 0, 0).passed
    assert (BenchResult(0, 0, 0, 0, 0, 0).card_error_rate, BenchResult(0, 0, 0, 0, 0, 0).expansion_error_rate) == (0, 0)


def test_reused_printing_ids():
    r = MatchResult(Enrichment({}, {}), {1: 1000, 2: 1000, 3: 1001, 4: 1000, 5: 1002, 6: 1002}, {}, {})
    assert run_bench.reused_printing_ids(r) == {1000: [1, 2, 4], 1002: [5, 6]}
