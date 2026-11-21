"""Run the matcher bench on Magic and print the matcher's coverage on the games matched by name.

Yu-Gi-Oh! (yaml-yugi) and Riftbound (Riftcodex) are matched to Cardmarket by name. Run this after changing the
matcher: it exits with 1 when more than 1% of the Magic cards or expansions are wrong.

Usage: python scripts/run_bench.py [--cache-dir DIR] [--errors N]

Downloads Cardmarket Magic and MTGJSON, runs the matcher on MTGJSON's printings ignoring their Cardmarket ids, and
judges every produced card against those ids: a card is wrong when its set code or number differs. It runs twice:
with one printing per (set, number), which exercises the pairs, and with one per finish, which exercises the fill-in.
MTGJSON names cards the way Cardmarket does more often than other sources, so this is a regression guard, not an
estimate of the error on the other games. Then, for yugioh and riftbound, prints the share of Cardmarket singles with
a card. --cache-dir keeps every download as a pickle (delete it to refresh); --errors prints that many wrong cards
and expansions.
"""

import argparse
import pickle
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cardledger_database.http import make_client  # noqa: E402
from cardledger_database.matcher import MatchResult, match  # noqa: E402
from cardledger_database.model import Enrichment, SourceCard, normalize_number  # noqa: E402
from cardledger_database.sources import cardmarket, mtgjson, riftcodex, yamlyugi  # noqa: E402

MAGIC = 1  # Cardmarket idGame
NAME_GAMES = {"yugioh": (3, yamlyugi.fetch), "riftbound": (22, riftcodex.fetch)}
MAX_ERROR_RATE = 0.01  # above this the matcher is not good enough for the games matched by name


@dataclass(frozen=True)
class BenchResult:
    card_matches: int  # pairs whose Cardmarket product is in the truth
    card_errors: int
    fill_matches: int  # fill-ins whose Cardmarket product is in the truth
    fill_errors: int
    expansion_matches: int  # paired expansions with a single in the truth
    expansion_errors: int

    @property
    def card_error_rate(self) -> float:
        judged = self.card_matches + self.fill_matches
        return (self.card_errors + self.fill_errors) / judged if judged else 0

    @property
    def expansion_error_rate(self) -> float:
        return self.expansion_errors / self.expansion_matches if self.expansion_matches else 0

    @property
    def passed(self) -> bool:
        return self.card_error_rate <= MAX_ERROR_RATE and self.expansion_error_rate <= MAX_ERROR_RATE


def _key(card: SourceCard) -> tuple[str, str]:
    return card.set_code, normalize_number(card.collector_number)


def wrong(result: MatchResult, truth: Enrichment) -> tuple[list[int], list[int]]:
    """(wrong pairs, wrong fill-ins): judged cards whose set code or number differs from the truth."""
    pairs, fills = [], []
    for pid, card in result.enrichment.cards.items():
        if pid in truth.cards and _key(card) != _key(truth.cards[pid]):
            (pairs if pid in result.card_pairs else fills).append(pid)
    return pairs, fills


def true_sets(result: MatchResult, truth: Enrichment) -> dict[int, Counter]:
    """{idExpansion: Counter(true set code)} of each expansion's singles."""
    counts: dict[int, Counter] = defaultdict(Counter)
    for pid, id_expansion in result.single_expansions.items():
        if pid in truth.cards:
            counts[id_expansion][truth.cards[pid].set_code] += 1
    return counts


def bench(result: MatchResult, truth: Enrichment) -> BenchResult:
    """A card is wrong when its (set code, number) is not the truth's; an expansion is wrong when its set code is not
    the most frequent true set code of its singles (ties: any of them)."""
    judged = [pid for pid in result.enrichment.cards if pid in truth.cards]
    pairs = sum(1 for pid in judged if pid in result.card_pairs)
    wrong_pairs, wrong_fills = wrong(result, truth)
    counts = true_sets(result, truth)
    expansion_matches = expansion_errors = 0
    for id_expansion, code in result.enrichment.expansions.items():
        if id_expansion not in result.expansion_pairs or not counts.get(id_expansion):
            continue
        expansion_matches += 1
        if counts[id_expansion][code] != max(counts[id_expansion].values()):
            expansion_errors += 1
    return BenchResult(pairs, len(wrong_pairs), len(judged) - pairs, len(wrong_fills), expansion_matches,
                       expansion_errors)


def reused_printing_ids(result: MatchResult) -> dict[int, list[int]]:
    """{printing id: [idProduct, ...]} of the printings paired with more than one Cardmarket product."""
    by_printing: dict[int, list[int]] = defaultdict(list)
    for pid, printing_id in sorted(result.card_pairs.items()):
        by_printing[printing_id].append(pid)
    return {printing_id: pids for printing_id, pids in by_printing.items() if len(pids) > 1}


def cached(cache_dir, name, fetch):
    if cache_dir is None:
        return fetch()
    path = cache_dir / f"{name}.pickle"
    if path.exists():
        return pickle.loads(path.read_bytes())
    value = fetch()
    path.write_bytes(pickle.dumps(value))
    return value


def print_errors(products, result, truth, limit):
    names = {p.id_product: p for p in products}
    for label, pids in zip(("pairs", "fill-ins"), wrong(result, truth)):
        print(f"\nwrong {label} ({len(pids)}):")
        for pid in pids[:limit]:
            got, right = result.enrichment.cards[pid], truth.cards[pid]
            print(f"  {pid} {names[pid].name!r} exp {names[pid].id_expansion} -> {_key(got)} | true: {_key(right)}")
    counts = true_sets(result, truth)
    wrong_exp = [(e, c) for e, c in result.enrichment.expansions.items()
                 if e in result.expansion_pairs and counts.get(e) and counts[e][c] != max(counts[e].values())]
    print(f"\nwrong expansions ({len(wrong_exp)}):")
    for e, c in wrong_exp[:limit]:
        print(f"  exp {e} -> {c!r} | true: {counts[e].most_common(3)}")


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--errors", type=int, default=0)
    args = parser.parse_args(argv)
    if args.cache_dir:
        args.cache_dir.mkdir(parents=True, exist_ok=True)

    with make_client() as client:
        products, singles = cached(args.cache_dir, "cardmarket_1", lambda: cardmarket.fetch_products(client, MAGIC))
        truth = cached(args.cache_dir, "mtgjson", lambda: mtgjson.fetch(client))
        passed = True
        magic_singles = sum(1 for p in products if p.id_category in singles)
        # Pairs: one printing per (set, number). Fill-ins: one printing per finish, which Cardmarket sells as one
        # product, so the matcher must fill them in.
        for label, per_finish in (("pairs", False), ("fill-ins", True)):
            groups, printings = cached(args.cache_dir, f"mtgjson_input_{label}",
                                       lambda: mtgjson.matcher_input(client, per_finish=per_finish))
            result = match(products, groups, printings, singles)
            bench_result = bench(result, truth)
            passed = passed and bench_result.passed
            print(f"magic ({label}): {bench_result}")
            print(f"  card_error_rate={bench_result.card_error_rate:.4f} "
                  f"expansion_error_rate={bench_result.expansion_error_rate:.4f} passed={bench_result.passed}")
            print(f"  {len(result.card_pairs)} pairs, {len(result.enrichment.cards) - len(result.card_pairs)} "
                  f"fill-ins, {len(result.expansion_pairs)} expansion pairs, "
                  f"coverage {len(result.enrichment.cards) / magic_singles:.1%} of {magic_singles} singles")
            if args.errors:
                print_errors(products, result, truth, args.errors)

        for slug, (cm_game, fetch) in NAME_GAMES.items():
            products, singles = cached(args.cache_dir, f"cardmarket_{cm_game}",
                                       lambda: cardmarket.fetch_products(client, cm_game))
            groups, printings = cached(args.cache_dir, slug, lambda: fetch(client))
            result = match(products, groups, printings, singles)
            n = sum(1 for p in products if p.id_category in singles)
            print(f"{slug}: {len(result.card_pairs)} pairs, {len(result.enrichment.cards) - len(result.card_pairs)} "
                  f"fill-ins, {len(result.expansion_pairs)} expansion pairs, "
                  f"coverage {len(result.enrichment.cards) / n if n else 0:.1%} of {n} singles")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
