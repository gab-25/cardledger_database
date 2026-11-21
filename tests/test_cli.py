import json
import os
from pathlib import Path

import pytest

from cardledger_database import cli
from cardledger_database.catalog import catalog_key
from cardledger_database.matcher import MatchResult
from cardledger_database.model import GAMES, CmProduct, Enrichment, SourceCard, SourceError, SourceSet
from cardledger_database.sources import cardmarket, lorcanajson, mtgjson, punkrecords, riftcodex, tcgdex, yamlyugi


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
        monkeypatch.setattr(riftcodex, "fetch", lambda client: ([], ["riftbound"]))
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
    assert s3.objects[("bucket", "catalog/v1/lorcana.json")] == (tmp_path / catalog_key("lorcana")).read_bytes()


def test_upload_refuses_missing_files(fakes, tmp_path, monkeypatch):
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    cli.generate(["lorcana"], tmp_path, tmp_path, client=None)
    s3 = FakeS3()

    with pytest.raises(SourceError, match="mtg.json"):
        cli.upload(["lorcana", "mtg"], tmp_path, s3)
    assert s3.objects == {}


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
