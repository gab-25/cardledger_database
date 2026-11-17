"""Run the matcher bench on Magic and print the matcher's coverage on the TCGCSV games.

The TCGCSV games (One Piece, Yu-Gi-Oh!, Riftbound) are matched to Cardmarket by name. Run this after changing the
matcher: it exits with 1 when more than 1% of the Magic matches are wrong.

Usage: python scripts/run_bench.py [--cache-dir DIR] [--errors N]

Downloads Cardmarket Magic, TCGCSV category 1 and Scryfall (the truth), runs the matcher on Magic ignoring
Scryfall, and prints the BenchResult; then, for onepiece/yugioh/riftbound, the share of Cardmarket singles with a
match. --cache-dir keeps every download as a pickle so tuning rounds don't download again (delete it to refresh);
--errors prints that many wrong card and expansion pairs.
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
from cardledger_database.sources import cardmarket, scryfall, tcgcsv  # noqa: E402
from cardledger_database.sources.tcgcsv import TcgProduct  # noqa: E402

MAGIC = (1, 1)  # Cardmarket idGame, TCGCSV category
TCGCSV_GAMES = {"onepiece": (18, 68), "yugioh": (3, 2), "riftbound": (22, 89)}
MAX_ERROR_RATE = 0.01  # above this the matcher is not good enough for the TCGCSV games


@dataclass(frozen=True)
class BenchResult:
    card_matches: int  # card pairs whose Cardmarket product is in the truth
    card_errors: int
    expansion_matches: int  # expansion pairs whose expansion has a true card in a TCGCSV group
    expansion_errors: int

    @property
    def card_error_rate(self) -> float:
        return self.card_errors / self.card_matches if self.card_matches else 0

    @property
    def expansion_error_rate(self) -> float:
        return self.expansion_errors / self.expansion_matches if self.expansion_matches else 0

    @property
    def passed(self) -> bool:
        return self.card_error_rate <= MAX_ERROR_RATE and self.expansion_error_rate <= MAX_ERROR_RATE


def true_groups(result: MatchResult, truth: dict[int, set[int]], tcg: list[TcgProduct]) -> dict[int, Counter]:
    """{idExpansion: Counter(groupId)} of the true TCGplayer cards of each expansion's singles."""
    group_of = {p.product_id: p.group_id for p in tcg}
    counts: dict[int, Counter] = defaultdict(Counter)
    for id_product, id_expansion in result.single_expansions.items():
        for product_id in truth.get(id_product, ()):
            if product_id in group_of:
                counts[id_expansion][group_of[product_id]] += 1
    return counts


def bench(result: MatchResult, truth: dict[int, set[int]], tcg: list[TcgProduct]) -> BenchResult:
    """A card is wrong when its product is in the truth and the paired productId is not among its true ids; an
    expansion is wrong when its group is not the most frequent group of its true cards (ties: any of them)."""
    judged = [(pid, product_id) for pid, product_id in result.card_pairs.items() if pid in truth]
    card_errors = sum(1 for pid, product_id in judged if product_id not in truth[pid])
    counts = true_groups(result, truth, tcg)
    expansion_matches = expansion_errors = 0
    for id_expansion, group_id in result.expansion_pairs.items():
        if not counts.get(id_expansion):
            continue
        expansion_matches += 1
        top = max(counts[id_expansion].values())
        if counts[id_expansion][group_id] != top:
            expansion_errors += 1
    return BenchResult(len(judged), card_errors, expansion_matches, expansion_errors)


def reused_product_ids(result: MatchResult) -> dict[int, list[int]]:
    """{productId: [idProduct, ...]} of the TCGplayer cards paired with more than one Cardmarket product."""
    by_product: dict[int, list[int]] = defaultdict(list)
    for pid, product_id in sorted(result.card_pairs.items()):
        by_product[product_id].append(pid)
    return {product_id: pids for product_id, pids in by_product.items() if len(pids) > 1}


def cached(cache_dir, name, fetch):
    if cache_dir is None:
        return fetch()
    path = cache_dir / f"{name}.pickle"
    if path.exists():
        return pickle.loads(path.read_bytes())
    value = fetch()
    path.write_bytes(pickle.dumps(value))
    return value


def print_errors(products, groups, tcg, result, truth, limit):
    names = {p.id_product: p for p in products}
    tcg_by_id = {p.product_id: p for p in tcg}
    group_names = {g.group_id: g.name for g in groups}
    wrong = [(pid, t) for pid, t in result.card_pairs.items() if pid in truth and t not in truth[pid]]
    print(f"\nwrong cards ({len(wrong)}):")
    for pid, t in wrong[:limit]:
        p, got = names[pid], tcg_by_id.get(t)
        right = [tcg_by_id[i] for i in truth[pid] if i in tcg_by_id]
        print(f"  {pid} {p.name!r} exp {p.id_expansion} -> {got.name!r} #{got.number} [{group_names[got.group_id]}]"
              f" | true: {[(r.name, r.number, group_names[r.group_id]) for r in right]}")
    counts = true_groups(result, truth, tcg)
    wrong_exp = [(e, g) for e, g in result.expansion_pairs.items()
                 if counts.get(e) and counts[e][g] != max(counts[e].values())]
    print(f"\nwrong expansions ({len(wrong_exp)}):")
    for e, g in wrong_exp[:limit]:
        sample = next(p.name for p in products if p.id_expansion == e)
        print(f"  exp {e} (e.g. {sample!r}) -> {group_names[g]!r} | true: "
              f"{[(group_names.get(k), n) for k, n in counts[e].most_common(3)]}")


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--errors", type=int, default=0)
    args = parser.parse_args(argv)
    if args.cache_dir:
        args.cache_dir.mkdir(parents=True, exist_ok=True)

    with make_client() as client:
        cm_game, category = MAGIC
        products, singles = cached(args.cache_dir, "cardmarket_1", lambda: cardmarket.fetch_products(client, cm_game))
        groups, tcg = cached(args.cache_dir, f"tcgcsv_{category}", lambda: tcgcsv.fetch(client, category))
        _, truth = cached(args.cache_dir, "scryfall", lambda: scryfall.fetch(client))
        result = match(products, groups, tcg, singles)
        bench_result = bench(result, truth, tcg)
        reused = reused_product_ids(result)
        print(bench_result)
        print(f"card_error_rate={bench_result.card_error_rate:.4f} "
              f"expansion_error_rate={bench_result.expansion_error_rate:.4f} passed={bench_result.passed}")
        magic_singles = sum(1 for p in products if p.id_category in singles)
        print(f"magic: {len(result.card_pairs)} card pairs, {len(result.expansion_pairs)} expansion pairs, "
              f"coverage {len(result.card_pairs) / magic_singles:.1%} of {magic_singles} singles")
        print(f"magic: {len(reused)} TCGplayer productIds paired with more than one product")
        if args.errors:
            print_errors(products, groups, tcg, result, truth, args.errors)

        for slug, (cm_game, category) in TCGCSV_GAMES.items():
            products, singles = cached(args.cache_dir, f"cardmarket_{cm_game}",
                                       lambda: cardmarket.fetch_products(client, cm_game))
            groups, tcg = cached(args.cache_dir, f"tcgcsv_{category}", lambda: tcgcsv.fetch(client, category))
            result = match(products, groups, tcg, singles)
            n = sum(1 for p in products if p.id_category in singles)
            print(f"{slug}: {len(result.card_pairs)} card pairs, {len(result.expansion_pairs)} expansion pairs, "
                  f"coverage {len(result.card_pairs) / n if n else 0:.1%} of {n} singles, "
                  f"{len(reused_product_ids(result))} reused productIds")
    return 0 if bench_result.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
