"""punk-records' One Piece card data (from the official Bandai card list), joined to Cardmarket on the card code printed
in the product name. Images only where the variant is unambiguous: a wrong image is worse than none."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import httpx

from ..model import CmProduct, Enrichment, SourceCard, SourceError, SourceSet, code_of

BASE_URL = "https://raw.githubusercontent.com/buhbbl/punk-records/main/english"
PACKS_URL = f"{BASE_URL}/packs.json"
CARDS_URL = f"{BASE_URL}/index/cards_by_id.json"

MIN_SHARE = 0.8  # share of an expansion's coded singles its pack must have

RARITIES = {
    "Common": "C", "Uncommon": "UC", "Rare": "R", "SuperRare": "SR", "SecretRare": "SEC", "Leader": "L",
    "Special": "SP", "TreasureRare": "TR", "Promo": "P",
}

_SIMPLE_LABEL = re.compile(r"^([A-Za-z]+)-(\d+)$")


@dataclass(frozen=True)
class _Variant:
    key: str
    pack_id: str
    rarity: str
    image: str


def set_code(pack_id: str, label: str | None) -> str:
    """"OP-05" -> "op05", "OP14-EB04" -> "op14-eb04"; a pack without a label -> "pk<pack id>"."""
    label = (label or "").strip()
    if not label:
        return f"pk{pack_id}"
    m = _SIMPLE_LABEL.match(label)
    return (m.group(1) + m.group(2)).lower() if m else label.lower()


def _sets(packs: dict) -> tuple[dict[str, str], dict[str, SourceSet]]:
    """({pack id: set code}, {set code: set})."""
    codes: dict[str, str] = {}
    sets: dict[str, SourceSet] = {}
    for pack_id, pack in packs.items():
        parts = pack.get("title_parts") or {}
        code = set_code(str(pack_id), parts.get("label"))
        codes[str(pack_id)] = code
        sets[code] = SourceSet(code, parts.get("title") or pack.get("raw_title") or code.upper(), None)
    return codes, sets


def _variants(cards: dict) -> dict[str, list[_Variant]]:
    """{base code: its variants}, the base code first when present."""
    by_code: dict[str, list[_Variant]] = defaultdict(list)
    for key, c in cards.items():
        by_code[key.split("_")[0]].append(
            _Variant(key, str(c["pack_id"]), c.get("rarity") or "", (c.get("img_url") or "")[:500]))
    for code, variants in by_code.items():
        variants.sort(key=lambda v: (v.key != code, v.key))
    return by_code


def _expansion_packs(coded: list[tuple[CmProduct, str]], variants: dict[str, list[_Variant]]) -> dict[int, str]:
    """{Cardmarket expansion id: pack id} when one pack has at least MIN_SHARE of the expansion's coded singles and
    strictly more than the second."""
    votes: dict[int, Counter] = defaultdict(Counter)
    totals: Counter = Counter()
    for p, code in coded:
        totals[p.id_expansion] += 1
        for pack_id in {v.pack_id for v in variants[code]}:
            votes[p.id_expansion][pack_id] += 1
    mapped = {}
    for exp, counter in votes.items():
        (pack_id, n), *rest = counter.most_common(2)
        if n / totals[exp] >= MIN_SHARE and (not rest or n > rest[0][1]):
            mapped[exp] = pack_id
    return mapped


def build(packs: dict, cards: dict, products: list[CmProduct], singles: set[int]) -> Enrichment:
    """The One Piece enrichment from the two punk-records files and the Cardmarket products."""
    packs = packs if isinstance(packs, dict) else {}
    cards = cards if isinstance(cards, dict) else {}
    pack_codes, sets = _sets(packs)
    variants = _variants(cards)
    coded = [(p, code) for p in products
             if p.id_category in singles and (code := code_of(p.name)) is not None and code in variants]
    exp_packs = _expansion_packs(coded, variants)
    per_expansion = Counter((code, p.id_expansion) for p, code in coded)

    def code_of_pack(pack_id: str) -> str:
        return pack_codes.get(pack_id) or set_code(pack_id, None)

    # Several Cardmarket expansions (booster, pre-release, reprint) can map to one pack. The one with the most coded
    # singles is the pack's main expansion and takes its code; the others get their own code and no images, so their
    # rows stay apart and never show the booster's artwork for a printing that may differ.
    coded_per_expansion = Counter(p.id_expansion for p, _ in coded)
    main_expansion: dict[str, int] = {}
    for exp, pack_id in sorted(exp_packs.items()):
        best = main_expansion.get(pack_id)
        if best is None or coded_per_expansion[exp] > coded_per_expansion[best]:
            main_expansion[pack_id] = exp
    expansions: dict[int, str] = {}
    for exp, pack_id in exp_packs.items():
        code = code_of_pack(pack_id)
        if main_expansion[pack_id] != exp:
            base = sets.get(code)
            name = f"{base.name if base else code.upper()} (Cardmarket #{exp})"
            code = f"{code}-cm{exp}"
            sets[code] = SourceSet(code, name, None)
        expansions[exp] = code

    result: dict[int, SourceCard] = {}
    for p, code in coded:
        pack_id = exp_packs.get(p.id_expansion)
        if pack_id is not None and main_expansion[pack_id] == p.id_expansion:
            in_pack = [v for v in variants[code] if v.pack_id == pack_id]
            if len(in_pack) == 1 and per_expansion[(code, p.id_expansion)] == 1:
                v = in_pack[0]
                result[p.id_product] = SourceCard(
                    expansions[p.id_expansion], code, RARITIES.get(v.rarity, v.rarity), v.image, v.image)
                continue
        rarities = {v.rarity for v in variants[code]}
        if len(rarities) == 1:
            rarity = rarities.pop()
            set_code_ = expansions.get(p.id_expansion) or code_of_pack(variants[code][0].pack_id)
            result[p.id_product] = SourceCard(set_code_, code, RARITIES.get(rarity, rarity), "", "")
    return Enrichment(cards=result, sets=sets, expansions=expansions)


def fetch(client: httpx.Client, products: list[CmProduct], singles: set[int]) -> Enrichment:
    data = {}
    for name, url in (("packs.json", PACKS_URL), ("cards_by_id.json", CARDS_URL)):
        try:
            response = client.get(url)
            response.raise_for_status()
            data[name] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"punk-records {name}: {type(exc).__name__}: {exc}") from exc
    try:
        return build(data["packs.json"], data["cards_by_id.json"], products, singles)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"punk-records: {type(exc).__name__}: {exc}") from exc
