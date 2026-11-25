"""A game's catalog: assembled from the Cardmarket products and a source, written as one JSON document."""

import json
from collections import Counter, defaultdict

from .model import CmProduct, Enrichment, Game, GameCatalog, Product, SourceSet, code_of, normalize_number

FORMAT_VERSION = 1


def catalog_key(slug: str) -> str:
    """The path of a game's file, the same under the output folder and in the bucket."""
    return f"catalog/v{FORMAT_VERSION}/{slug}.json"


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


def _shared_images(products: list[CmProduct], enrichment: Enrichment,
                   single_category_ids: set[int]) -> dict[tuple[int, str], tuple[str, str]]:
    """{(Cardmarket expansion id, name): (image, large image)} for the singles whose versions with an image all
    show the same one; names with differing images are left out (no image beats a wrong one)."""
    images: dict[tuple[int, str], set[tuple[str, str]]] = defaultdict(set)
    for p in products:
        card = enrichment.cards.get(p.id_product)
        if p.id_category in single_category_ids and card is not None and card.image_url:
            images[(p.id_expansion, p.name)].add((card.image_url, card.image_large_url))
    return {key: next(iter(found)) for key, found in images.items() if len(found) == 1}


def assemble(game: Game, products: list[CmProduct], enrichment: Enrichment,
             single_category_ids: set[int]) -> GameCatalog:
    main = _main_sets(products, enrichment, single_category_ids)
    shared = _shared_images(products, enrichment, single_category_ids) if game.share_images else {}
    rows: list[Product] = []
    stand_ins: dict[str, SourceSet] = {}
    skipped = enriched = 0
    for p in products:
        card = enrichment.cards.get(p.id_product)
        if card is not None:
            code, number, rarity = card.set_code, normalize_number(card.collector_number), card.rarity
            image, large = card.image_url, card.image_large_url
            enriched += 1
        else:
            code = main.get(p.id_expansion)
            number = normalize_number(code_of(p.name))  # the code printed in the name ("Sabo (OP13-004)"), if any
            rarity = image = large = ""
            if code is None:
                if not game.keep_unmatched:
                    skipped += 1
                    continue
                code = f"cmx{p.id_expansion}"
                stand_ins[code] = enrichment.sets.get(code) or SourceSet(
                    code, f"Cardmarket expansion #{p.id_expansion}", None)
        if not image and p.id_category in single_category_ids:
            image, large = shared.get((p.id_expansion, p.name), (image, large))
        rows.append(Product(p.id_product, p.name[:200], code, number[:40], rarity[:40], image[:500], large[:500],
                            p.id_category, p.category_name))
    sets = {**enrichment.sets, **stand_ins}
    expansions = [
        sets.get(code) or SourceSet(code, code.upper(), None) for code in sorted({r.expansion_code for r in rows})
    ]
    return GameCatalog(game.slug, expansions, rows, skipped, enriched)


def encode(catalog: GameCatalog) -> bytes:
    """One JSON object, {"expansions": [...], "products": [...]}, both sorted; the same catalog always gives the same
    bytes."""
    expansions = [{
        "code": e.code,
        "name": e.name,
        "released_at": e.released_at.isoformat() if e.released_at else None,
        "language": e.language,
    } for e in sorted(catalog.expansions, key=lambda e: e.code)]
    products = [{
        "id_product": p.id_product,
        "name": p.name,
        "expansion_code": p.expansion_code,
        "number": p.number,
        "rarity": p.rarity,
        "image_url": p.image_url,
        "image_large_url": p.image_large_url,
        "category_id": p.category_id,
        "category_name": p.category_name,
    } for p in sorted(catalog.products, key=lambda p: (p.expansion_code, p.id_product))]
    doc = {"expansions": expansions, "products": products}
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")

