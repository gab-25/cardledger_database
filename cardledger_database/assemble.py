from collections import Counter, defaultdict

from .games import Game
from .model import CmProduct, Enrichment, GameCatalog, Product, SourceSet, code_of, normalize_number


def _main_sets(products: list[CmProduct], enrichment: Enrichment, single_category_ids: set[int]) -> dict[int, str]:
    """{Cardmarket expansion id: set code}: the known mapping, else the set most of its singles belong to
    (ties: the shortest code, then alphabetical)."""
    counts: dict[int, Counter] = defaultdict(Counter)
    for p in products:
        card = enrichment.cards.get(p.id_product)
        if p.id_category in single_category_ids and card is not None:
            counts[p.id_expansion][card.set_code] += 1
    voted = {exp: min(c.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0] for exp, c in counts.items()}
    return {**voted, **enrichment.expansions}


def assemble(game: Game, products: list[CmProduct], enrichment: Enrichment,
             single_category_ids: set[int]) -> GameCatalog:
    main = _main_sets(products, enrichment, single_category_ids)
    rows: list[Product] = []
    stand_ins: dict[str, SourceSet] = {}
    skipped = 0
    for p in products:
        card = enrichment.cards.get(p.id_product)
        if card is not None:
            code, number, rarity = card.set_code, normalize_number(card.collector_number), card.rarity
            image, large = card.image_url, card.image_large_url
        else:
            code = main.get(p.id_expansion)
            number = normalize_number(code_of(p.name))  # the code printed in the name ("Sabo (OP13-004)"), if any
            rarity = image = large = ""
            if code is None:
                if not game.keep_unmatched:
                    skipped += 1
                    continue
                code = f"cmx{p.id_expansion}"
                stand_ins[code] = SourceSet(code, f"Cardmarket expansion #{p.id_expansion}", None)
        rows.append(Product(p.id_product, p.name[:200], code, number[:40], rarity[:40], image[:500], large[:500],
                            p.id_category, p.category_name))
    sets = {**enrichment.sets, **stand_ins}
    expansions = [
        sets.get(code) or SourceSet(code, code.upper(), None) for code in sorted({r.expansion_code for r in rows})
    ]
    return GameCatalog(game.slug, expansions, rows, skipped)
