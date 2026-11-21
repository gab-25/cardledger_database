import gzip
import json

import pytest

from cardledger_database import cli
from cardledger_database.catalog import catalog_key
from cardledger_database.matcher import MatchResult
from cardledger_database.model import GAMES, CmProduct, Enrichment, SourceCard, SourceError, SourceSet
from cardledger_database.sources import cardmarket, lorcanajson, mtgjson, punkrecords, tcgcsv, tcgdex


def cm_products(n):
    return [CmProduct(i, f"Card {i}", 1, "Singles", 7, None) for i in range(1, n + 1)]


def enrichment_for(products, code):
    cards = {p.id_product: SourceCard(code, str(p.id_product), "common", f"img/{p.id_product}", "") for p in products}
    return Enrichment(cards, {code: SourceSet(code, "Set", None)}, {})


class Fakes:
    """Fake sources: every game has 4 products, all enriched; a slug in `broken` makes its source fail."""

    def __init__(self, monkeypatch):
        self.broken: set[str] = set()
        self.cache_dirs = []
        slugs_by_category = {g.tcgcsv_category: slug for slug, g in GAMES.items() if g.source == "tcgcsv"}

        def source(slug):
            if slug in self.broken:
                raise SourceError(f"{slug} source is down")
            return enrichment_for(cm_products(4), code=slug)

        def fake_tcgdex(client, cache_dir):
            self.cache_dirs.append(cache_dir)
            return source("pokemon")

        monkeypatch.setattr(cardmarket, "fetch_products", lambda client, cm_id: (cm_products(4), {1}))
        monkeypatch.setattr(mtgjson, "fetch", lambda client: source("mtg"))
        monkeypatch.setattr(lorcanajson, "fetch", lambda client: source("lorcana"))
        monkeypatch.setattr(punkrecords, "fetch", lambda client, products, singles: source("onepiece"))
        monkeypatch.setattr(tcgdex, "fetch", fake_tcgdex)
        monkeypatch.setattr(tcgcsv, "fetch", lambda client, category: ([], [category]))
        monkeypatch.setattr(
            cli, "match", lambda products, groups, tcg, singles: MatchResult(source(slugs_by_category[tcg[0]]), {}, {}, {})
        )


@pytest.fixture
def fakes(monkeypatch):
    return Fakes(monkeypatch)


def products_in(path):
    lines = [json.loads(x) for x in gzip.decompress(path.read_bytes()).decode().splitlines()]
    return [x for x in lines if x["type"] == "product"]


def test_generate_writes_every_game(fakes, tmp_path):
    assert cli.generate(list(GAMES), tmp_path, tmp_path / "cache", client=None) == []
    for slug in GAMES:
        products = products_in(tmp_path / catalog_key(slug))
        assert len(products) == 4 and {p["expansion_code"] for p in products} == {slug}
    assert fakes.cache_dirs == [tmp_path / "cache"]


def test_failed_game_keeps_its_previous_file(fakes, tmp_path):
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)
    before = (tmp_path / catalog_key("lorcana")).read_bytes()
    fakes.broken.add("lorcana")

    assert cli.generate(["lorcana", "pokemon"], tmp_path, tmp_path, client=None) == ["lorcana"]
    assert (tmp_path / catalog_key("lorcana")).read_bytes() == before
    assert (tmp_path / catalog_key("pokemon")).is_file()


def test_generate_exit_code(fakes, tmp_path, capsys):
    assert cli.main(["generate", "--game", "lorcana", "--out", str(tmp_path), "--cache", str(tmp_path)]) == 0
    assert "lorcana: written" in capsys.readouterr().out
    fakes.broken.add("lorcana")
    assert cli.main(["generate", "--game", "lorcana", "--out", str(tmp_path), "--cache", str(tmp_path)]) == 1


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body


def test_upload_copies_the_files_under_the_same_keys(fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    cli.generate(["lorcana", "mtg"], tmp_path, tmp_path, client=None)
    s3 = FakeS3()

    assert cli.upload(["lorcana", "mtg"], tmp_path, s3) == [catalog_key("lorcana"), catalog_key("mtg")]
    assert s3.objects[("bucket", "catalog/v1/lorcana.jsonl.gz")] == (tmp_path / catalog_key("lorcana")).read_bytes()


def test_upload_refuses_missing_files(fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)
    s3 = FakeS3()

    with pytest.raises(SourceError, match="mtg.jsonl.gz"):
        cli.upload(["lorcana", "mtg"], tmp_path, s3)
    assert s3.objects == {}


def test_upload_requires_the_bucket(tmp_path, monkeypatch):
    monkeypatch.delenv("CARDLEDGER_S3_BUCKET", raising=False)
    with pytest.raises(SourceError, match="CARDLEDGER_S3_BUCKET"):
        cli.upload(["lorcana"], tmp_path, FakeS3())
    assert cli.main(["upload", "--out", str(tmp_path)]) == 1


def test_onepiece_does_not_use_tcgcsv(fakes, tmp_path, monkeypatch):
    def no_tcgcsv(client, category):
        raise AssertionError("TCGCSV used for One Piece")

    monkeypatch.setattr(tcgcsv, "fetch", no_tcgcsv)
    assert cli.generate(["onepiece"], tmp_path, tmp_path, client=None) == []
    assert {p["expansion_code"] for p in products_in(tmp_path / catalog_key("onepiece"))} == {"onepiece"}
