"""Links Cardmarket products to TCGCSV (TCGplayer) cards by name or printed code, for the games without a
dedicated source. A wrong match shows another variant's image, a missing one only shows none: every rule
prefers no match over a doubtful one."""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .model import CmProduct, Enrichment, SourceCard, SourceSet
from .sources.tcgcsv import TcgGroup, TcgProduct

MIN_COMMON = 2  # keys an expansion must share with its group
MIN_SHARE = 0.3  # ... and as a share of the expansion's keys
MIN_MARGIN = 0.1  # F1 lead of the best group over the second best

CODE = re.compile(r"\(([A-Z0-9]{1,6}-(?:[A-Z]{2})?\d{1,4}[A-Z]?)\)")
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_SUFFIX = re.compile(r" - .*$")
_NOT_KEPT = re.compile(r"[^a-z0-9 /]")
_SPACES = re.compile(r"\s+")

SMALL_IMAGE, LARGE_IMAGE = "_200w", "_in_1000x1000"


@dataclass(frozen=True)
class MatchResult:
    enrichment: Enrichment
    card_pairs: dict[int, int]  # Cardmarket idProduct -> TCGplayer productId
    expansion_pairs: dict[int, int]  # Cardmarket idExpansion -> TCGplayer groupId
    single_expansions: dict[int, int] = field(default_factory=dict)  # every single's idProduct -> idExpansion


def code_of(name: str) -> str | None:
    """The card code printed in a Cardmarket name ("Luffy (OP01-001)" -> "OP01-001"), if any."""
    m = CODE.search(name)
    return m.group(1) if m else None


def normalize_name(name: str) -> str:
    """ASCII, lower case, without [...], (...) and " - ...", only [a-z0-9 /], single spaces."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = _SUFFIX.sub("", _BRACKETS.sub("", text)).lower()
    return _SPACES.sub(" ", _NOT_KEPT.sub("", text)).strip()


def card_key(name: str) -> tuple[str, str]:
    """("code", code) when the Cardmarket name carries a code, else ("name", normalized name)."""
    code = code_of(name)
    return ("code", code) if code else ("name", normalize_name(name))


def tcg_key(product: TcgProduct, mode: str) -> str:
    return product.number.upper() if mode == "code" else normalize_name(product.name)


def expansion_mode(singles: list[CmProduct]) -> str:
    """"code" when most singles of the expansion carry a code, else "name"."""
    with_code = sum(1 for p in singles if code_of(p.name))
    return "code" if with_code * 2 > len(singles) else "name"


def cm_keys(singles: list[CmProduct], mode: str) -> dict[int, str]:
    """{idProduct: key} of the singles that have a key in this mode."""
    keys = {}
    for p in singles:
        key = code_of(p.name) if mode == "code" else normalize_name(p.name)
        if key:
            keys[p.id_product] = key
    return keys


def f1(common: int, exp_keys: int, group_keys: int) -> float:
    return 2 * common / (exp_keys + group_keys) if exp_keys + group_keys else 0.0


def best_group(exp_keys: set[str], group_keys: dict[int, set[str]], index: dict[str, set[int]]) -> int | None:
    """The group sharing the most keys by F1, when it shares at least MIN_COMMON keys and MIN_SHARE of the
    expansion's keys and leads the second best by MIN_MARGIN; else None."""
    common: Counter = Counter()
    for key in exp_keys:
        for group_id in index.get(key, ()):
            common[group_id] += 1
    scored = sorted(((f1(n, len(exp_keys), len(group_keys[g])), n, g) for g, n in common.items()), reverse=True)
    if not scored:
        return None
    score, n, group_id = scored[0]
    if n < MIN_COMMON or n < MIN_SHARE * len(exp_keys):
        return None
    if len(scored) > 1 and score - scored[1][0] < MIN_MARGIN:
        return None
    return group_id


def pair_cards(keys: dict[int, str], cards: list[TcgProduct], mode: str) -> dict[int, TcgProduct]:
    """{idProduct: card} for the keys found exactly once on each side; never by id order."""
    cm_count = Counter(keys.values())
    by_key: dict[str, list[TcgProduct]] = defaultdict(list)
    for card in cards:
        by_key[tcg_key(card, mode)].append(card)
    return {
        pid: by_key[key][0] for pid, key in keys.items() if cm_count[key] == 1 and len(by_key.get(key, ())) == 1
    }


def set_codes(groups: list[TcgGroup]) -> dict[int, str]:
    """{groupId: set code}: the lower-case abbreviation, "tcg<groupId>" when empty or shared by groups."""
    counts = Counter(g.abbreviation.strip().lower() for g in groups)
    return {
        g.group_id: abbr if abbr and counts[abbr] == 1 else f"tcg{g.group_id}"
        for g in groups
        for abbr in [g.abbreviation.strip().lower()]
    }


def large_image(url: str) -> str:
    return url.replace(SMALL_IMAGE, LARGE_IMAGE)


def match(products: list[CmProduct], groups: list[TcgGroup], tcg: list[TcgProduct],
          single_category_ids: set[int]) -> MatchResult:
    codes = set_codes(groups)
    by_group: dict[int, list[TcgProduct]] = defaultdict(list)
    for card in tcg:
        by_group[card.group_id].append(card)
    group_keys = {mode: {g: {tcg_key(c, mode) for c in cards} - {""} for g, cards in by_group.items()}
                  for mode in ("code", "name")}
    index: dict[str, dict[str, set[int]]] = {"code": defaultdict(set), "name": defaultdict(set)}
    for mode, keys_by_group in group_keys.items():
        for group_id, keys in keys_by_group.items():
            for key in keys:
                index[mode][key].add(group_id)

    singles: dict[int, list[CmProduct]] = defaultdict(list)
    for p in products:
        if p.id_category in single_category_ids:
            singles[p.id_expansion].append(p)

    groups_by_id = {g.group_id: g for g in groups}
    cards: dict[int, SourceCard] = {}
    card_pairs: dict[int, int] = {}
    expansion_pairs: dict[int, int] = {}
    for id_expansion, items in singles.items():
        mode = expansion_mode(items)
        keys = cm_keys(items, mode)
        group_id = best_group(set(keys.values()), group_keys[mode], index[mode])
        if group_id is None or group_id not in groups_by_id:
            continue
        expansion_pairs[id_expansion] = group_id
        names = {p.id_product: p.name for p in items}
        for pid, card in pair_cards(keys, by_group[group_id], mode).items():
            card_pairs[pid] = card.product_id
            cards[pid] = SourceCard(codes[group_id], code_of(names[pid]) or card.number, card.rarity,
                                    card.image_url, large_image(card.image_url))

    sets = {
        codes[g]: SourceSet(codes[g], groups_by_id[g].name, groups_by_id[g].published_on)
        for g in set(expansion_pairs.values())
    }
    expansions = {exp: codes[g] for exp, g in expansion_pairs.items()}
    single_expansions = {p.id_product: id_expansion for id_expansion, items in singles.items() for p in items}
    return MatchResult(Enrichment(cards, sets, expansions), card_pairs, expansion_pairs, single_expansions)
