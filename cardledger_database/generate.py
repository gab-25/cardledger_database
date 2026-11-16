"""Generate each game's catalog, check it and publish it with the manifest."""

import logging
import time
from datetime import UTC, datetime

from . import cardmarket, corrections
from .assemble import assemble
from .bench import BenchResult, bench
from .errors import QualityError
from .games import GAMES
from .matcher import match
from .model import Enrichment, GameCatalog
from .output import MANIFEST_KEY, catalog_key, encode, encode_manifest, game_entry, read_manifest
from .sources import lorcanajson, scryfall, tcgcsv, tcgdex
from .store import Store

logger = logging.getLogger(__name__)

# a game is not published with fewer products, or fewer products enriched by its source, than this share of the
# previous publication
PRUNE_MIN_SHARE = 0.5

# slug -> (source, TCGCSV category)
SOURCES: dict[str, tuple[str, int | None]] = {
    "mtg": ("scryfall", None),
    "pokemon": ("tcgdex", None),
    "lorcana": ("lorcanajson", None),
    "onepiece": ("tcgcsv", 68),
    "yugioh": ("tcgcsv", 2),
    "riftbound": ("tcgcsv", 89),
}
MAGIC_BENCH_CATEGORY = 1  # TCGCSV category of Magic, matched against Scryfall's truth


class _Downloads:
    """The Magic downloads of one run, shared by the bench and the Magic catalog."""

    def __init__(self):
        self._values = {}

    def get(self, name, fetch):
        if name not in self._values:
            self._values[name] = fetch()
        return self._values[name]


def _products(client, slug, downloads):
    cm_id = GAMES[slug].cm_id
    return downloads.get(f"cardmarket {cm_id}", lambda: cardmarket.fetch_products(client, cm_id))


def _scryfall(client, downloads):
    return downloads.get("scryfall", lambda: scryfall.fetch(client))


def magic_bench(client, downloads: _Downloads | None = None) -> BenchResult:
    downloads = downloads or _Downloads()
    products, singles = _products(client, "mtg", downloads)
    groups, tcg = tcgcsv.fetch(client, MAGIC_BENCH_CATEGORY)
    _, truth = _scryfall(client, downloads)
    return bench(match(products, groups, tcg, singles), truth, tcg)


def _enrichment(slug, client, store, products, singles, downloads) -> Enrichment:
    kind, category = SOURCES[slug]
    if kind == "scryfall":
        return _scryfall(client, downloads)[0]
    if kind == "tcgdex":
        return tcgdex.fetch(client, store)
    if kind == "lorcanajson":
        return lorcanajson.fetch(client)
    groups, tcg = tcgcsv.fetch(client, category)
    return match(products, groups, tcg, singles).enrichment


def _build(slug, client, store, bench_result, downloads) -> tuple[GameCatalog, list[str]]:
    if SOURCES[slug][0] == "tcgcsv":
        if bench_result is None:
            raise QualityError("the Magic bench did not run: TCGCSV games are not published")
        if not bench_result.passed:
            raise QualityError(
                f"the Magic bench failed (card errors {bench_result.card_error_rate:.2%}, expansion errors "
                f"{bench_result.expansion_error_rate:.2%}): TCGCSV games are not published"
            )
    products, singles = _products(client, slug, downloads)
    enrichment = _enrichment(slug, client, store, products, singles, downloads)
    enrichment, warnings = corrections.apply(corrections.load(slug), products, enrichment)
    for warning in warnings:
        logger.warning("%s: %s", slug, warning)
    return assemble(GAMES[slug], products, enrichment, singles), warnings


def generate_game(slug: str, client, store: Store, bench_result: BenchResult | None) -> GameCatalog:
    """Cardmarket products -> source -> corrections -> assemble. TCGCSV games need a passing Magic bench."""
    return _build(slug, client, store, bench_result, _Downloads())[0]


def _check_size(catalog: GameCatalog, previous: dict | None) -> None:
    """Blocks a game that shrank: fewer products, or (the games keeping unmatched products under a stand-in
    expansion would not shrink with an empty source) fewer products with a source card. An entry without
    `enriched` (older manifests) skips that check."""
    if not previous:
        return
    if len(catalog.products) < PRUNE_MIN_SHARE * previous["products"]:
        raise QualityError(
            f"{len(catalog.products)} products, less than {PRUNE_MIN_SHARE:.0%} of the {previous['products']} "
            "previously published"
        )
    if previous.get("enriched") is not None and catalog.enriched < PRUNE_MIN_SHARE * previous["enriched"]:
        raise QualityError(
            f"{catalog.enriched} products enriched by the source, less than {PRUNE_MIN_SHARE:.0%} of the "
            f"{previous['enriched']} previously published"
        )


def _counts(catalog: GameCatalog, warnings: list[str], seconds: float) -> str:
    with_image = sum(1 for p in catalog.products if p.image_url)
    share = with_image / len(catalog.products) if catalog.products else 0
    return (f"products={len(catalog.products)} enriched={catalog.enriched} expansions={len(catalog.expansions)} with_image={share:.1%} "
            f"skipped={catalog.skipped} correction_warnings={len(warnings)} seconds={seconds:.0f}")


def run(slugs: list[str], store: Store, client) -> dict[str, str]:
    """Generate and publish the games; returns {slug: "published" | "unchanged" | error message}."""
    unknown = [s for s in slugs if s not in GAMES or s not in SOURCES]
    if unknown:
        raise ValueError(f"unknown games: {', '.join(unknown)}")
    previous = read_manifest(store.get(MANIFEST_KEY))
    games = dict(previous["games"])
    downloads = _Downloads()
    now = datetime.now(UTC)

    bench_result = None
    if any(SOURCES[s][0] == "tcgcsv" for s in slugs):
        try:
            bench_result = magic_bench(client, downloads)
            logger.info("magic bench: card errors %.2f%%, expansion errors %.2f%%, passed=%s",
                        100 * bench_result.card_error_rate, 100 * bench_result.expansion_error_rate,
                        bench_result.passed)
        except Exception as exc:
            logger.error("magic bench: failed: %s", exc, exc_info=True)

    outcomes: dict[str, str] = {}
    for slug in slugs:
        start = time.monotonic()
        try:
            catalog, warnings = _build(slug, client, store, bench_result, downloads)
            _check_size(catalog, previous["games"].get(slug))
            data = encode(catalog)
            entry = game_entry(slug, data, catalog, now)
            old = previous["games"].get(slug)
            if old and old.get("sha256") == entry["sha256"]:
                games[slug] = {**old, "enriched": entry["enriched"]}  # an older entry may lack the count
                outcomes[slug] = "unchanged"
            else:
                store.put(catalog_key(slug), data)
                games[slug] = entry
                outcomes[slug] = "published"
            logger.info("%s: %s %s", slug, outcomes[slug], _counts(catalog, warnings, time.monotonic() - start))
        except Exception as exc:
            outcomes[slug] = str(exc) or type(exc).__name__
            expected = isinstance(exc, QualityError)
            logger.error("%s: failed after %.0fs: %s", slug, time.monotonic() - start, outcomes[slug],
                         exc_info=not expected)

    store.put(MANIFEST_KEY, encode_manifest(games, now))
    return outcomes
