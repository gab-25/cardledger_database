import json

import pytest

from cardledger_database import cli, generate
from cardledger_database.bench import BenchResult
from cardledger_database.errors import QualityError, SourceError
from cardledger_database.matcher import MatchResult
from cardledger_database.model import CmProduct, Enrichment, SourceCard, SourceSet
from cardledger_database.output import MANIFEST_KEY, catalog_key, read_manifest
from cardledger_database.sources import lorcanajson, scryfall, tcgcsv, tcgdex
from cardledger_database.store import LocalStore

PASSING = BenchResult(100, 0, 10, 0)
FAILING = BenchResult(100, 5, 10, 0)


def cm_products(n, id_expansion=7):
    return [CmProduct(i, f"Card {i}", 1, "Singles", id_expansion, None) for i in range(1, n + 1)]


def enrichment_for(products, code="set"):
    cards = {p.id_product: SourceCard(code, str(p.id_product), "common", f"img/{p.id_product}", "") for p in products}
    return Enrichment(cards, {code: SourceSet(code, "Set", None)}, {})


class Fakes:
    """Fake sources: each game gets `sizes[slug]` products; a slug in `broken` makes its source fail."""

    def __init__(self, monkeypatch):
        self.sizes = {"mtg": 4, "pokemon": 4, "lorcana": 4, "onepiece": 4, "yugioh": 4, "riftbound": 4}
        self.broken: set[str] = set()
        self.calls: list[str] = []
        self.bench = PASSING
        slugs_by_cm = {game.cm_id: slug for slug, game in generate.GAMES.items()}
        slugs_by_category = {cat: slug for slug, (kind, cat) in generate.SOURCES.items() if kind == "tcgcsv"}

        def fetch_products(client, cm_id):
            self.calls.append(f"cardmarket {cm_id}")
            return cm_products(self.sizes[slugs_by_cm[cm_id]]), {1}

        def source(slug):
            if slug in self.broken:
                raise SourceError(f"{slug} source is down")
            return enrichment_for(cm_products(self.sizes[slug]), code=slug)

        def fake_scryfall(client):
            self.calls.append("scryfall")
            return source("mtg"), {}

        def fake_tcgcsv(client, category_id):
            self.calls.append(f"tcgcsv {category_id}")
            return [], [category_id]

        def fake_match(products, groups, tcg, singles):
            if tcg == [generate.MAGIC_BENCH_CATEGORY]:
                return MatchResult(Enrichment({}, {}), {}, {}, {})
            return MatchResult(source(slugs_by_category[tcg[0]]), {}, {}, {})

        monkeypatch.setattr(generate.cardmarket, "fetch_products", fetch_products)
        monkeypatch.setattr(scryfall, "fetch", fake_scryfall)
        monkeypatch.setattr(lorcanajson, "fetch", lambda client: source("lorcana"))
        monkeypatch.setattr(tcgdex, "fetch", lambda client, store: source("pokemon"))
        monkeypatch.setattr(tcgcsv, "fetch", fake_tcgcsv)
        monkeypatch.setattr(generate, "match", fake_match)
        monkeypatch.setattr(generate, "bench", lambda result, truth, tcg: self.bench)


@pytest.fixture
def fakes(monkeypatch):
    return Fakes(monkeypatch)


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path)


def manifest(store):
    return read_manifest(store.get(MANIFEST_KEY))


def test_games_are_published_with_manifest(fakes, store):
    outcomes = generate.run(list(generate.GAMES), store, client=None)

    assert outcomes == dict.fromkeys(generate.GAMES, "published")
    games = manifest(store)["games"]
    assert set(games) == set(generate.GAMES)
    for slug in generate.GAMES:
        assert store.get(catalog_key(slug)) is not None
        assert games[slug]["products"] == 4
    assert manifest(store)["generated_at"].endswith("+00:00")


def test_magic_bench_reuses_the_magic_downloads(fakes, store):
    generate.run(["mtg", "onepiece"], store, client=None)

    assert fakes.calls.count("scryfall") == 1
    assert fakes.calls.count("cardmarket 1") == 1
    assert fakes.calls.count(f"tcgcsv {generate.MAGIC_BENCH_CATEGORY}") == 1


def test_bench_is_skipped_without_tcgcsv_games(fakes, store):
    generate.run(["lorcana"], store, client=None)

    assert "scryfall" not in fakes.calls
    assert f"tcgcsv {generate.MAGIC_BENCH_CATEGORY}" not in fakes.calls


def test_shrunk_game_is_not_published(fakes, store):
    generate.run(["lorcana"], store, client=None)
    before = store.get(catalog_key("lorcana"))
    entry = manifest(store)["games"]["lorcana"]

    fakes.sizes["lorcana"] = 1  # 1 < 50% of 4
    outcomes = generate.run(["lorcana"], store, client=None)

    assert outcomes["lorcana"] not in ("published", "unchanged")
    assert "50%" in outcomes["lorcana"]
    assert store.get(catalog_key("lorcana")) == before
    assert manifest(store)["games"]["lorcana"] == entry


def test_half_size_game_is_published(fakes, store):
    generate.run(["lorcana"], store, client=None)
    fakes.sizes["lorcana"] = 2

    assert generate.run(["lorcana"], store, client=None) == {"lorcana": "published"}
    assert manifest(store)["games"]["lorcana"]["products"] == 2


def test_failed_game_keeps_previous_entry(fakes, store):
    generate.run(["lorcana", "pokemon"], store, client=None)
    entry = manifest(store)["games"]["lorcana"]

    fakes.broken.add("lorcana")
    fakes.sizes["pokemon"] = 5
    outcomes = generate.run(["lorcana", "pokemon"], store, client=None)

    assert outcomes["lorcana"] == "lorcana source is down"
    assert outcomes["pokemon"] == "published"
    games = manifest(store)["games"]
    assert games["lorcana"] == entry
    assert games["pokemon"]["products"] == 5


def test_games_not_requested_keep_their_entry(fakes, store):
    generate.run(["lorcana"], store, client=None)
    entry = manifest(store)["games"]["lorcana"]

    generate.run(["pokemon"], store, client=None)

    assert manifest(store)["games"]["lorcana"] == entry


def test_unchanged_file_is_not_rewritten(fakes, store, monkeypatch):
    generate.run(["lorcana"], store, client=None)
    entry = manifest(store)["games"]["lorcana"]
    writes = []
    put = store.put
    monkeypatch.setattr(store, "put", lambda key, data: (writes.append(key), put(key, data)))

    outcomes = generate.run(["lorcana"], store, client=None)

    assert outcomes == {"lorcana": "unchanged"}
    assert writes == [MANIFEST_KEY]
    assert manifest(store)["games"]["lorcana"] == entry


def test_tcgcsv_games_need_a_passing_bench(fakes, store):
    for bench_result in (None, FAILING):
        with pytest.raises(QualityError):
            generate.generate_game("onepiece", None, store, bench_result)

    catalog = generate.generate_game("onepiece", None, store, PASSING)
    assert len(catalog.products) == 4
    assert generate.generate_game("lorcana", None, store, None).game == "lorcana"


def test_failing_bench_blocks_only_tcgcsv_games(fakes, store):
    fakes.bench = FAILING

    outcomes = generate.run(["lorcana", "yugioh"], store, client=None)

    assert outcomes["lorcana"] == "published"
    assert "bench" in outcomes["yugioh"]
    assert "yugioh" not in manifest(store)["games"]


def test_unknown_game_is_rejected(fakes, store):
    with pytest.raises(ValueError):
        generate.run(["chess"], store, client=None)


def test_cli_exit_code_is_1_when_a_game_fails(fakes, tmp_path):
    fakes.broken.add("lorcana")

    assert cli.main(["generate", "--game", "lorcana", "--game", "pokemon", "--out", str(tmp_path)]) == 1
    assert json.loads((tmp_path / MANIFEST_KEY).read_text())["games"].keys() == {"pokemon"}


def test_cli_exit_code_is_0_when_all_games_succeed(fakes, tmp_path, capsys):
    assert cli.main(["generate", "--game", "lorcana", "--out", str(tmp_path)]) == 0
    assert "lorcana: published" in capsys.readouterr().out
