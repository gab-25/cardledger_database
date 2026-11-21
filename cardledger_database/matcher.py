"""Links Cardmarket products to a source's printings by name or printed code, for the games whose source has no
Cardmarket ids. A wrong match shows another variant's image, a missing one only shows none: every rule
prefers no match over a doubtful one."""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from .model import CmProduct, Enrichment, SourceCard, SourceSet, code_of

MIN_COMMON = 2  # keys an expansion must share with its group
MIN_SHARE = 0.3  # ... and as a share of the expansion's keys
MIN_MARGIN = 0.1  # F1 lead of the best group over the second best
MAX_PRINTING_RATIO = 1.5  # printings per key of the expansion over printings per key of the group

_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_SUFFIX = re.compile(r" - .*$")
_NOT_KEPT = re.compile(r"[^a-z0-9 /]")
_SPACES = re.compile(r"\s+")

_SLUG = re.compile(r"[^a-z0-9]+")
MAX_CODE = 40


@dataclass(frozen=True)
class Group:
    """A set of the source."""
    group_id: int
    name: str
    abbreviation: str
    published_on: date | None


@dataclass(frozen=True)
class Printing:
    """A card printing of the source, in one group."""
    printing_id: int
    group_id: int
    name: str
    number: str
    rarity: str
    image_url: str
    image_large_url: str


@dataclass(frozen=True)
class MatchResult:
    enrichment: Enrichment
    card_pairs: dict[int, int]  # Cardmarket idProduct -> printing id
    expansion_pairs: dict[int, int]  # Cardmarket idExpansion -> group id
    single_expansions: dict[int, int] = field(default_factory=dict)  # every single's idProduct -> idExpansion


def normalize_name(name: str) -> str:
    """ASCII, lower case, without [...], (...) and " - ...", only [a-z0-9 /], single spaces."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = _SUFFIX.sub("", _BRACKETS.sub("", text)).lower()
    return _SPACES.sub(" ", _NOT_KEPT.sub("", text)).strip()


def card_key(name: str) -> tuple[str, str]:
    """("code", code) when the Cardmarket name carries a code, else ("name", normalized name)."""
    code = code_of(name)
    return ("code", code) if code else ("name", normalize_name(name))


def tcg_key(product: Printing, mode: str) -> str:
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


def group_scores(exp_keys: set[str], group_keys: dict[int, set[str]],
                 index: dict[str, set[int]]) -> dict[int, tuple[float, int]]:
    """{groupId: (F1, common keys)} of every group sharing a key with the expansion."""
    common: Counter = Counter()
    for key in exp_keys:
        for group_id in index.get(key, ()):
            common[group_id] += 1
    return {g: (f1(n, len(exp_keys), len(group_keys[g])), n) for g, n in common.items()}


def best_group(exp_keys: set[str], scores: dict[int, tuple[float, int]]) -> int | None:
    """The group sharing the most keys by F1, when it shares at least MIN_COMMON keys and MIN_SHARE of the
    expansion's keys and leads the second best by MIN_MARGIN; else None."""
    scored = sorted(((score, n, g) for g, (score, n) in scores.items()), reverse=True)
    if not scored:
        return None
    score, n, group_id = scored[0]
    if n < MIN_COMMON or n < MIN_SHARE * len(exp_keys):
        return None
    if len(scored) > 1 and score - scored[1][0] < MIN_MARGIN:
        return None
    return group_id


def best_expansion(group_id: int, scores: dict[int, dict[int, tuple[float, int]]]) -> int | None:
    """The expansion with the highest F1 for the group, None on a tie: a group holds one Cardmarket expansion."""
    ranked = sorted(((s[group_id][0], e) for e, s in scores.items() if group_id in s), reverse=True)
    if not ranked or len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def printing_ratio(keys: dict[int, str], cards: list[Printing], mode: str) -> float:
    """Printings per key of the expansion over those of the group: well above 1 when the group holds only some of
    the expansion's printings (e.g. promo pack stamps but not the prerelease ones)."""
    card_keys = [k for k in (tcg_key(c, mode) for c in cards) if k]
    if not keys or not card_keys:
        return 0.0
    return (len(keys) / len(set(keys.values()))) / (len(card_keys) / len(set(card_keys)))


def pair_cards(keys: dict[int, str], cards: list[Printing], mode: str) -> dict[int, Printing]:
    """{idProduct: card} for the keys found exactly once on each side; never by id order."""
    cm_count = Counter(keys.values())
    by_key: dict[str, list[Printing]] = defaultdict(list)
    for card in cards:
        by_key[tcg_key(card, mode)].append(card)
    return {
        pid: by_key[key][0] for pid, key in keys.items() if cm_count[key] == 1 and len(by_key.get(key, ())) == 1
    }


def _fallback_code(group: Group) -> str:
    """"<abbreviation or set>-<slug of the name>": stable across runs, unlike the (often synthetic) group id."""
    abbr = _SLUG.sub("", group.abbreviation.strip().lower()) or "set"
    slug = _SLUG.sub("-", group.name.lower()).strip("-")
    return f"{abbr}-{slug}"[:MAX_CODE].rstrip("-")


def fill_in(keys: dict[int, str], cards: list[Printing], mode: str, paired: set[int]) -> dict[int, list[Printing]]:
    """{idProduct: candidate printings} for the unpaired singles whose key's printings all share one number, when
    the expansion has no more singles with that key than the group has printings with it (rarity variants pass; a
    regular + oversized pair against one printing does not)."""
    cm_count = Counter(keys.values())
    by_key: dict[str, list[Printing]] = defaultdict(list)
    for card in cards:
        by_key[tcg_key(card, mode)].append(card)
    found = {}
    for pid, key in keys.items():
        candidates = by_key.get(key, [])
        if pid in paired or not candidates or cm_count[key] > len(candidates):
            continue
        if len({c.number for c in candidates}) == 1:
            found[pid] = candidates
    return found


def _shared(values) -> str:
    values = set(values)
    return values.pop() if len(values) == 1 else ""


def set_codes(groups: list[Group]) -> dict[int, str]:
    """{group id: set code}: the lower-case abbreviation when unique, else the fallback code (plus "-<group id>" for
    the groups after the first, by id, sharing it)."""
    counts = Counter(g.abbreviation.strip().lower() for g in groups)
    codes: dict[int, str] = {}
    taken: set[str] = set()
    for g in sorted(groups, key=lambda g: g.group_id):
        abbr = g.abbreviation.strip().lower()
        code = abbr if abbr and counts[abbr] == 1 else _fallback_code(g)
        if code in taken:
            code = f"{code[:MAX_CODE - len(str(g.group_id)) - 1]}-{g.group_id}"
        taken.add(code)
        codes[g.group_id] = code
    return codes


def match(products: list[CmProduct], groups: list[Group], tcg: list[Printing],
          single_category_ids: set[int]) -> MatchResult:
    """Pair each expansion with its best group when the group's best expansion is that one too and holds as many
    printings per key, then pair the cards whose key is unique on both sides."""
    codes = set_codes(groups)
    by_group: dict[int, list[Printing]] = defaultdict(list)
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
    modes = {e: expansion_mode(items) for e, items in singles.items()}
    keys_of = {e: cm_keys(items, modes[e]) for e, items in singles.items()}
    scores = {e: group_scores(set(keys_of[e].values()), group_keys[modes[e]], index[modes[e]]) for e in singles}
    for id_expansion, items in singles.items():
        mode, keys = modes[id_expansion], keys_of[id_expansion]
        group_id = best_group(set(keys.values()), scores[id_expansion])
        if group_id is None or group_id not in groups_by_id:
            continue
        if best_expansion(group_id, scores) != id_expansion:
            continue
        if printing_ratio(keys, by_group[group_id], mode) > MAX_PRINTING_RATIO:
            continue
        expansion_pairs[id_expansion] = group_id
        names = {p.id_product: p.name for p in items}
        for pid, card in pair_cards(keys, by_group[group_id], mode).items():
            card_pairs[pid] = card.printing_id
            cards[pid] = SourceCard(codes[group_id], code_of(names[pid]) or card.number, card.rarity,
                                    card.image_url, card.image_large_url)
        for pid, candidates in fill_in(keys, by_group[group_id], mode, set(cards)).items():
            images = {(c.image_url, c.image_large_url) for c in candidates}
            image, large = images.pop() if len(images) == 1 else ("", "")
            if not image:
                image = large = ""
            cards[pid] = SourceCard(codes[group_id], code_of(names[pid]) or candidates[0].number,
                                    _shared(c.rarity for c in candidates), image, large)

    sets = {
        codes[g]: SourceSet(codes[g], groups_by_id[g].name, groups_by_id[g].published_on)
        for g in set(expansion_pairs.values())
    }
    expansions = {exp: codes[g] for exp, g in expansion_pairs.items()}
    single_expansions = {p.id_product: id_expansion for id_expansion, items in singles.items() for p in items}
    return MatchResult(Enrichment(cards, sets, expansions), card_pairs, expansion_pairs, single_expansions)
