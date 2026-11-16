"""The Magic bench: run the matcher on Magic, whose Scryfall data gives the true TCGplayer ids of each Cardmarket
product, and count the wrong matches. Above MAX_ERROR_RATE the TCGCSV games are not published."""

from collections import Counter, defaultdict
from dataclasses import dataclass

from .matcher import MatchResult
from .sources.tcgcsv import TcgProduct

MAX_ERROR_RATE = 0.01


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
