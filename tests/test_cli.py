import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest
from cardledger_vision import Header, Index, encode, index_key

from cardledger_database import cli
from cardledger_database.catalog import catalog_key
from cardledger_database.matcher import MatchResult
from cardledger_database.model import GAMES, CmProduct, Enrichment, SourceCard, SourceError, SourceSet
from cardledger_database.sources import cardmarket, lorcanajson, mtgjson, openrift, punkrecords, tcgdex, yamlyugi


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
        self.index_broken: set[str] = set()
        self.indexed = []  # (slug, catalog sha256, cache_dir, confirm_only) of every index built

        def fake_build_index(slug, data, sha256, client, cache_dir, confirm_only=False):
            if slug in self.index_broken:
                raise RuntimeError(f"{slug} images are down")
            assert sha256 == hashlib.sha256(data).hexdigest()
            self.indexed.append((slug, sha256, cache_dir, confirm_only))
            return encode(Header(slug, "2026-01-01T00:00:00Z", sha256, confirm_only), [])

        monkeypatch.setattr(cli, "build_index", fake_build_index)

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
        monkeypatch.setattr(yamlyugi, "fetch", lambda client: ([], ["yugioh"]))
        monkeypatch.setattr(openrift, "fetch", lambda client: source("riftbound"))
        monkeypatch.setattr(
            cli, "match", lambda products, groups, printings, singles: MatchResult(source(printings[0]), {}, {}, {})
        )


@pytest.fixture
def fakes(monkeypatch):
    return Fakes(monkeypatch)


def products_in(path):
    return json.loads(path.read_bytes())["products"]


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


def index_header(path):
    return Index.load(path.read_bytes()).header


def test_generate_writes_each_catalog_with_its_index(fakes, tmp_path):
    assert cli.generate(["lorcana", "mtg"], tmp_path, tmp_path / "cache", client=None) == []
    for slug in ("lorcana", "mtg"):
        header = index_header(tmp_path / index_key(slug))
        assert header.game == slug
        assert header.catalog_sha256 == hashlib.sha256((tmp_path / catalog_key(slug)).read_bytes()).hexdigest()
    assert {cache_dir for _, _, cache_dir, _ in fakes.indexed} == {tmp_path / "cache" / "vision"}


def test_every_game_is_confirm_only_for_now():
    assert all(game.confirm_only for game in GAMES.values())


def test_generate_passes_the_game_confirm_only(fakes, tmp_path, monkeypatch):
    monkeypatch.setitem(cli.GAMES, "mtg", dataclasses.replace(cli.GAMES["mtg"], confirm_only=False))
    cli.generate(["lorcana", "mtg"], tmp_path, tmp_path, client=None)
    assert [(slug, confirm_only) for slug, _, _, confirm_only in fakes.indexed] == [("lorcana", True), ("mtg", False)]
    assert index_header(tmp_path / index_key("lorcana")).confirm_only
    assert not index_header(tmp_path / index_key("mtg")).confirm_only


def test_generate_without_index(fakes, tmp_path):
    argv = ["generate", "--game", "lorcana", "--no-index", "--out", str(tmp_path), "--cache", str(tmp_path)]
    assert cli.main(argv) == 0
    assert (tmp_path / catalog_key("lorcana")).is_file()
    assert not (tmp_path / index_key("lorcana")).exists()
    assert fakes.indexed == []


def test_failed_index_keeps_the_previous_catalog_and_index(fakes, tmp_path):
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)
    before = (tmp_path / catalog_key("lorcana")).read_bytes(), (tmp_path / index_key("lorcana")).read_bytes()
    fakes.index_broken.add("lorcana")

    assert cli.generate(["lorcana", "mtg"], tmp_path, tmp_path, client=None) == ["lorcana"]
    assert ((tmp_path / catalog_key("lorcana")).read_bytes(), (tmp_path / index_key("lorcana")).read_bytes()) == before
    assert (tmp_path / index_key("mtg")).is_file()


def test_index_rebuilds_from_the_catalog_in_the_folder(fakes, tmp_path, capsys):
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None, index=False)
    sha = hashlib.sha256((tmp_path / catalog_key("lorcana")).read_bytes()).hexdigest()

    argv = ["index", "--game", "lorcana", "--game", "mtg", "--out", str(tmp_path), "--cache", str(tmp_path)]
    assert cli.main(argv) == 1
    assert index_header(tmp_path / index_key("lorcana")).catalog_sha256 == sha
    assert not (tmp_path / index_key("mtg")).exists()
    assert "mtg: no catalog, run generate first" in capsys.readouterr().out


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body


def test_upload_copies_the_files_under_the_same_keys(fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    cli.generate(["lorcana", "mtg"], tmp_path, tmp_path, client=None)
    s3 = FakeS3()

    assert cli.upload(["lorcana", "mtg"], tmp_path, s3) == [
        catalog_key("lorcana"), catalog_key("mtg"), index_key("lorcana"), index_key("mtg")
    ]
    assert s3.objects[("bucket", "catalog/v1/lorcana.json")] == (tmp_path / catalog_key("lorcana")).read_bytes()
    assert s3.objects[("bucket", "vision/v1/lorcana.bin")] == (tmp_path / index_key("lorcana")).read_bytes()


def test_upload_refuses_missing_files(fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)
    s3 = FakeS3()

    with pytest.raises(SourceError, match="mtg.json"):
        cli.upload(["lorcana", "mtg"], tmp_path, s3)
    assert s3.objects == {}


def test_upload_refuses_a_missing_index(fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)
    cli.generate(["mtg"], tmp_path, tmp_path, client=None, index=False)
    s3 = FakeS3()

    with pytest.raises(SourceError, match="mtg.bin"):
        cli.upload(["lorcana", "mtg"], tmp_path, s3)
    assert s3.objects == {}


def test_upload_refuses_an_index_of_another_catalog(fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)
    (tmp_path / catalog_key("lorcana")).write_bytes(b'{"expansions":[],"products":[]}')
    s3 = FakeS3()

    with pytest.raises(SourceError, match="another catalog"):
        cli.upload(["lorcana"], tmp_path, s3)
    assert s3.objects == {}


def test_upload_refuses_the_index_of_another_game(fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    cli.generate(["lorcana", "mtg"], tmp_path, tmp_path, client=None)
    (tmp_path / index_key("lorcana")).write_bytes((tmp_path / index_key("mtg")).read_bytes())

    with pytest.raises(SourceError, match="index of 'mtg'"):
        cli.upload(["lorcana"], tmp_path, FakeS3())


def test_upload_refuses_an_invalid_index(fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)
    (tmp_path / index_key("lorcana")).write_bytes(b"not an index")

    with pytest.raises(SourceError, match="not a valid index"):
        cli.upload(["lorcana"], tmp_path, FakeS3())


S3_ENV = {
    "CARDLEDGER_S3_BUCKET": "bucket",
    "CARDLEDGER_S3_ENDPOINT": "https://s3.example.com",
    "CARDLEDGER_S3_ACCESS_KEY_ID": "key",
    "CARDLEDGER_S3_SECRET_ACCESS_KEY": "secret",
    "CARDLEDGER_S3_REGION": "region",
}


def test_upload_builds_the_client_from_the_cardledger_variables(fakes, tmp_path, monkeypatch):
    import boto3

    for name, value in S3_ENV.items():
        monkeypatch.setenv(name, value)
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)
    calls = []
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: calls.append((args, kwargs)) or FakeS3())

    cli.upload(["lorcana"], tmp_path)
    assert calls == [(("s3",), {"endpoint_url": "https://s3.example.com", "aws_access_key_id": "key",
                                "aws_secret_access_key": "secret", "region_name": "region"})]


@pytest.mark.parametrize("name", ["CARDLEDGER_S3_ACCESS_KEY_ID", "CARDLEDGER_S3_SECRET_ACCESS_KEY",
                                  "CARDLEDGER_S3_REGION"])
def test_upload_requires_the_credentials_and_region(fakes, tmp_path, monkeypatch, name):
    for key, value in S3_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(name)
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)

    with pytest.raises(SourceError, match=name):
        cli.upload(["lorcana"], tmp_path)


def test_upload_requires_the_bucket(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CARDLEDGER_S3_BUCKET", raising=False)
    with pytest.raises(SourceError, match="CARDLEDGER_S3_BUCKET"):
        cli.upload(["lorcana"], tmp_path, FakeS3())
    assert cli.main(["upload", "--out", str(tmp_path)]) == 1


def test_upload_reads_dotenv_from_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CARDLEDGER_S3_BUCKET", raising=False)
    (tmp_path / ".env").write_text("CARDLEDGER_S3_BUCKET=from-dotenv\n")
    seen = []
    monkeypatch.setattr(cli, "upload", lambda slugs, out: seen.append(os.environ.get("CARDLEDGER_S3_BUCKET")))
    assert cli.main(["upload"]) == 0
    assert seen == ["from-dotenv"]


def test_dotenv_does_not_override_the_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "from-env")
    (tmp_path / ".env").write_text("CARDLEDGER_S3_BUCKET=from-dotenv\n")
    seen = []
    monkeypatch.setattr(cli, "upload", lambda slugs, out: seen.append(os.environ.get("CARDLEDGER_S3_BUCKET")))
    assert cli.main(["upload"]) == 0
    assert seen == ["from-env"]


def test_no_tcgcsv_left():
    root = Path(__file__).parent.parent
    files = [*root.glob("cardledger_database/**/*.py"), root / "README.md"]
    assert [str(f) for f in files if "tcgcsv" in f.read_text().lower()] == []


def test_ctrl_c_exits_130_without_waiting_for_threads(monkeypatch, capsys):
    def interrupted():
        raise KeyboardInterrupt

    exits = []
    monkeypatch.setattr(cli, "main", interrupted)
    monkeypatch.setattr(cli.os, "_exit", exits.append)
    cli.run()
    assert exits == [130]
    assert capsys.readouterr().err == "interrupted\n"


def test_generate_logs_the_stand_ins_without_a_name(fakes, tmp_path, monkeypatch, caplog):
    extra = [CmProduct(90 + i, f"Promo {i}", 1, "Singles", 9999, None) for i in range(2)]
    monkeypatch.setattr(cardmarket, "fetch_products", lambda client, cm_id: (cm_products(4) + extra, {1}))
    monkeypatch.setattr(cli.corrections, "load", lambda slug: cli.corrections.Corrections())
    with caplog.at_level("WARNING"):
        catalog = cli.generate_game("pokemon", None, tmp_path)
    assert {p.expansion_code for p in catalog.products} == {"pokemon", "cmx9999"}
    assert "pokemon: 1 Cardmarket expansion without a name" in caplog.text
    assert "expansions/pokemon.toml" in caplog.text and "9999 (2 products)" in caplog.text


def test_generate_does_not_log_named_stand_ins(fakes, tmp_path, monkeypatch, caplog):
    extra = [CmProduct(90, "Promo", 1, "Singles", 9999, None)]
    monkeypatch.setattr(cardmarket, "fetch_products", lambda client, cm_id: (cm_products(4) + extra, {1}))
    named = cli.corrections.Corrections((), ({"id_expansion": 9999, "name": "Promos"},))
    monkeypatch.setattr(cli.corrections, "load", lambda slug: named)
    with caplog.at_level("WARNING"):
        cli.generate_game("pokemon", None, tmp_path)
    assert "without a name" not in caplog.text
