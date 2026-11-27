import dataclasses
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from cardledger_vision import Header, Index, encode, index_key

from cardledger_database import cli, images
from cardledger_database.catalog import catalog_key
from cardledger_database.model import GAMES, CardSet, Row, SourceData, SourceError
from cardledger_database.sources import lorcanajson, openrift, punkrecords, scryfall, tcgdex, ygoprodeck


def image_url(slug, i):
    return f"https://img.example/{slug}/{i}.png"


def source_data(slug):
    """4 cards of one set, each with its own image."""
    rows = [Row(id=f"{slug}:{slug}:{i}", kind="card", name=f"Card {i}", set_code=slug, number=str(i), variant="",
                rarity="common", languages=("en",), finishes=(), category="", image_source=image_url(slug, i))
            for i in range(1, 5)]
    return SourceData(rows, [CardSet(slug, f"Set {slug}", None)])


def png(url):
    """A small PNG whose bytes differ per URL."""
    shade = hashlib.sha256(url.encode()).digest()[0]
    ok, buf = cv2.imencode(".png", np.full((14, 10, 3), shade, np.uint8))
    assert ok
    return buf.tobytes()


class Fakes:
    """Fake sources (every game has 4 cards with an image) and image downloads; a slug in `broken` makes its source
    fail, a URL in `missing_images` can't be downloaded."""

    def __init__(self, monkeypatch):
        self.broken: set[str] = set()
        self.missing_images: set[str] = set()
        self.cache_dirs = []
        self.index_broken: set[str] = set()
        self.indexed = []  # (slug, catalog sha256, image_root, cache_dir, confirm_only) of every index built

        def fake_build_index(slug, data, sha256, image_root, cache_dir, confirm_only=False):
            if slug in self.index_broken:
                raise RuntimeError(f"{slug} images are down")
            assert sha256 == hashlib.sha256(data).hexdigest()
            self.indexed.append((slug, sha256, image_root, cache_dir, confirm_only))
            return encode(Header(slug, "2026-01-01T00:00:00Z", sha256, confirm_only), [])

        monkeypatch.setattr(cli, "build_index", fake_build_index)
        monkeypatch.setattr(images, "_download",
                            lambda url, client: None if url in self.missing_images else png(url))

        def source(slug):
            if slug in self.broken:
                raise SourceError(f"{slug} source is down")
            return source_data(slug)

        def with_cache(slug):
            def fetch(client, cache_dir):
                self.cache_dirs.append(cache_dir)
                return source(slug)
            return fetch

        monkeypatch.setattr(scryfall, "fetch", with_cache("mtg"))
        monkeypatch.setattr(tcgdex, "fetch", with_cache("pokemon"))
        monkeypatch.setattr(lorcanajson, "fetch", lambda client: source("lorcana"))
        monkeypatch.setattr(punkrecords, "fetch", with_cache("onepiece"))
        monkeypatch.setattr(ygoprodeck, "fetch", with_cache("yugioh"))
        monkeypatch.setattr(openrift, "fetch", lambda client: source("riftbound"))


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    work = tmp_path / "work"  # an empty working directory: no corrections, no sealed products
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "bucket")
    return Fakes(monkeypatch)


def rows_in(path):
    return json.loads(path.read_bytes())["rows"]


def test_generate_writes_catalog_index_and_images(tmp_path, fakes):
    assert cli.generate(["lorcana"], tmp_path, tmp_path / "c", None) == []
    doc = json.loads((tmp_path / "catalog/v1/lorcana.json").read_bytes())
    assert "rows" in doc and (tmp_path / "vision/v1/lorcana.bin").is_file()
    assert all(r["image"].startswith("images/v1/lorcana/") for r in doc["rows"] if r["image"])
    assert len(doc["rows"]) == 4 and all(r["image"] and r["image_large"] for r in doc["rows"])
    for row in doc["rows"]:
        assert (tmp_path / "c/bucket" / row["image"]).is_file()
        assert (tmp_path / "c/bucket" / row["image_large"]).is_file()


def test_generate_writes_every_game(fakes, tmp_path):
    assert cli.generate(list(GAMES), tmp_path, tmp_path / "cache", client=None) == []
    for slug in GAMES:
        rows = rows_in(tmp_path / catalog_key(slug))
        assert len(rows) == 4 and {r["set_code"] for r in rows} == {slug}
    assert fakes.cache_dirs == [tmp_path / "cache"] * 4


def test_generate_logs_the_counts(fakes, tmp_path, caplog):
    fakes.missing_images.add(image_url("lorcana", 1))
    with caplog.at_level("INFO"):
        cli.generate(["lorcana"], tmp_path, tmp_path / "c", None)
    assert ("lorcana: written " + str(tmp_path / catalog_key("lorcana"))
            + " rows=4 cards=4 sealed=0 sets=1 merged=0 with_image=75.0% images_downloaded=3 images_failed=1"
            in caplog.text)


def test_sealed_and_corrections_are_applied(tmp_path, fakes, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "corrections").mkdir()
    (tmp_path / "corrections/lorcana.toml").write_text(
        '[[card]]\nid = "lorcana:lorcana:1"\nname = "Fixed"\n\n[[card]]\nid = "lorcana:lorcana:2"\ndrop = true\n'
        '\n[[card]]\nid = "lorcana:lorcana:99"\nname = "Stale"\n'
    )
    (tmp_path / "sealed").mkdir()
    (tmp_path / "sealed/lorcana.toml").write_text(
        '[[product]]\nkey = "box"\nname = "Booster Box"\nset_code = "lorcana"\ncategory = "booster_box"\n'
        'image_source = "https://img.example/box.png"\n'
    )
    catalog, stats = cli.generate_game("lorcana", None, tmp_path / "c")
    by_id = {r.id: r for r in catalog.rows}
    assert by_id["lorcana:lorcana:1"].name == "Fixed"
    assert "lorcana:lorcana:2" not in by_id
    assert by_id["lorcana:sealed:box"].kind == "sealed"
    assert by_id["lorcana:sealed:box"].image.startswith("images/v1/lorcana/")
    assert stats.downloaded == 4


def test_generate_logs_the_correction_warnings(tmp_path, fakes, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "corrections").mkdir()
    (tmp_path / "corrections/lorcana.toml").write_text('[[card]]\nid = "lorcana:lorcana:99"\nname = "Stale"\n')
    with caplog.at_level("WARNING"):
        cli.generate_game("lorcana", None, tmp_path / "c")
    assert "lorcana: correction for card lorcana:lorcana:99 skipped" in caplog.text


def test_failed_source_keeps_previous_files(tmp_path, fakes):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", client=None)
    before = (tmp_path / catalog_key("lorcana")).read_bytes(), (tmp_path / index_key("lorcana")).read_bytes()
    fakes.broken.add("lorcana")

    assert cli.generate(["lorcana", "pokemon"], tmp_path, tmp_path / "c", client=None) == ["lorcana"]
    assert ((tmp_path / catalog_key("lorcana")).read_bytes(), (tmp_path / index_key("lorcana")).read_bytes()) == before
    assert (tmp_path / catalog_key("pokemon")).is_file()


def test_generate_exit_code(fakes, tmp_path, capsys):
    assert cli.main(["generate", "--game", "lorcana", "--out", str(tmp_path), "--cache", str(tmp_path / "c")]) == 0
    assert "lorcana: written" in capsys.readouterr().out
    fakes.broken.add("lorcana")
    assert cli.main(["generate", "--game", "lorcana", "--out", str(tmp_path), "--cache", str(tmp_path / "c")]) == 1


def index_header(path):
    return Index.load(path.read_bytes()).header


def test_generate_writes_each_catalog_with_its_index(fakes, tmp_path):
    assert cli.generate(["lorcana", "mtg"], tmp_path, tmp_path / "cache", client=None) == []
    for slug in ("lorcana", "mtg"):
        header = index_header(tmp_path / index_key(slug))
        assert header.game == slug
        assert header.catalog_sha256 == hashlib.sha256((tmp_path / catalog_key(slug)).read_bytes()).hexdigest()
    assert {(root, cache_dir) for _, _, root, cache_dir, _ in fakes.indexed} == {
        (tmp_path / "cache" / "bucket", tmp_path / "cache" / "vision")
    }


def test_every_game_is_confirm_only_for_now():
    assert all(game.confirm_only for game in GAMES.values())


def test_generate_passes_the_game_confirm_only(fakes, tmp_path, monkeypatch):
    monkeypatch.setitem(cli.GAMES, "mtg", dataclasses.replace(cli.GAMES["mtg"], confirm_only=False))
    cli.generate(["lorcana", "mtg"], tmp_path, tmp_path / "c", client=None)
    assert [(slug, confirm_only) for slug, *_, confirm_only in fakes.indexed] == [("lorcana", True), ("mtg", False)]
    assert index_header(tmp_path / index_key("lorcana")).confirm_only
    assert not index_header(tmp_path / index_key("mtg")).confirm_only


def test_generate_without_index(fakes, tmp_path):
    argv = ["generate", "--game", "lorcana", "--no-index", "--out", str(tmp_path), "--cache", str(tmp_path / "c")]
    assert cli.main(argv) == 0
    assert (tmp_path / catalog_key("lorcana")).is_file()
    assert not (tmp_path / index_key("lorcana")).exists()
    assert fakes.indexed == []


def test_failed_index_keeps_the_previous_catalog_and_index(fakes, tmp_path):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", client=None)
    before = (tmp_path / catalog_key("lorcana")).read_bytes(), (tmp_path / index_key("lorcana")).read_bytes()
    fakes.index_broken.add("lorcana")
    fakes.missing_images.add(image_url("lorcana", 1))  # the new catalog differs from the previous one

    assert cli.generate(["lorcana", "mtg"], tmp_path, tmp_path / "c", client=None) == ["lorcana"]
    assert ((tmp_path / catalog_key("lorcana")).read_bytes(), (tmp_path / index_key("lorcana")).read_bytes()) == before
    assert (tmp_path / index_key("mtg")).is_file()


def test_index_rebuilds_from_the_catalog_in_the_folder(fakes, tmp_path, capsys):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", client=None, index=False)
    sha = hashlib.sha256((tmp_path / catalog_key("lorcana")).read_bytes()).hexdigest()

    argv = ["index", "--game", "lorcana", "--game", "mtg", "--out", str(tmp_path), "--cache", str(tmp_path / "c")]
    assert cli.main(argv) == 1
    assert index_header(tmp_path / index_key("lorcana")).catalog_sha256 == sha
    assert not (tmp_path / index_key("mtg")).exists()
    assert "mtg: no catalog, run generate first" in capsys.readouterr().out
    assert fakes.indexed[-1][2:4] == (tmp_path / "c" / "bucket", tmp_path / "c" / "vision")


class FakeS3:
    """The bucket's `existing` keys; `put` records (key, put_object arguments) in order. Listings come in pages of
    PAGE keys."""
    PAGE = 2

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.put = []
        self.listed = []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        assert Bucket == "bucket"
        self.listed.append((Prefix, ContinuationToken))
        keys = sorted(k for k in self.existing if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + self.PAGE]
        answer = {"KeyCount": len(page), "IsTruncated": start + self.PAGE < len(keys)}
        if page:
            answer["Contents"] = [{"Key": k} for k in page]
        if answer["IsTruncated"]:
            answer["NextContinuationToken"] = str(start + self.PAGE)
        return answer

    def put_object(self, Bucket, Key, Body, ContentType, **kwargs):
        assert Bucket == "bucket"
        self.put.append((Key, {"Body": Body, "ContentType": ContentType, **kwargs}))
        self.existing.add(Key)


def referenced(tmp_path, slug="lorcana"):
    return sorted(images.referenced((tmp_path / catalog_key(slug)).read_bytes()))


def test_upload_overwrites_v1_keys(tmp_path, fakes):
    cli.generate(["lorcana", "mtg"], tmp_path, tmp_path / "c", None)
    s3 = FakeS3(existing={"catalog/v1/lorcana.json", "vision/v1/lorcana.bin"})

    assert cli.upload(["lorcana", "mtg"], tmp_path, tmp_path / "c", s3) == []
    put = dict(s3.put)
    assert put["catalog/v1/lorcana.json"] == {"Body": (tmp_path / catalog_key("lorcana")).read_bytes(),
                                              "ContentType": "application/json"}
    assert put["vision/v1/lorcana.bin"] == {"Body": (tmp_path / index_key("lorcana")).read_bytes(),
                                            "ContentType": "application/octet-stream"}
    assert {"catalog/v1/mtg.json", "vision/v1/mtg.bin"} <= set(put)


def test_upload_order_images_index_catalog(tmp_path, fakes):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", None)
    paths = referenced(tmp_path)
    already = paths[0]
    s3 = FakeS3(existing={already, "images/v1/lorcana/old-s.webp", "images/v1/lorcana/old-l.webp",
                          "images/v1/mtg/other-s.webp"})

    assert cli.upload(["lorcana"], tmp_path, tmp_path / "c", s3) == []
    keys = [k for k, _ in s3.put]
    assert keys[-2:] == ["vision/v1/lorcana.bin", "catalog/v1/lorcana.json"]
    assert already not in keys and all(k.startswith("images/") for k in keys[:-2])
    assert sorted(keys[:-2]) == paths[1:]
    assert [prefix for prefix, _ in s3.listed] == ["images/v1/lorcana/"] * 2  # 3 keys under the prefix: 2 pages
    for key, kwargs in s3.put[:-2]:
        assert kwargs == {"Body": (tmp_path / "c/bucket" / key).read_bytes(), "ContentType": "image/webp",
                          "CacheControl": "public, max-age=31536000, immutable"}


def test_upload_logs_the_image_counts(tmp_path, fakes, caplog):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", None)
    s3 = FakeS3(existing={referenced(tmp_path)[0]})
    with caplog.at_level("INFO"):
        cli.upload(["lorcana"], tmp_path, tmp_path / "c", s3)
    assert "lorcana: uploaded images=7 already_there=1" in caplog.text


def test_upload_refuses_missing_rendition(tmp_path, fakes):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", None)
    (tmp_path / "c/bucket" / referenced(tmp_path)[0]).unlink()
    assert cli.upload(["lorcana"], tmp_path, tmp_path / "c", s3 := FakeS3()) == ["lorcana"] and s3.put == []


def test_upload_goes_on_with_the_other_games(tmp_path, fakes, caplog):
    cli.generate(["lorcana", "mtg"], tmp_path, tmp_path / "c", None)
    (tmp_path / "c/bucket" / referenced(tmp_path)[0]).unlink()
    s3 = FakeS3()
    with caplog.at_level("ERROR"):
        assert cli.upload(["lorcana", "mtg"], tmp_path, tmp_path / "c", s3) == ["lorcana"]
    assert "lorcana: " in caplog.text and "run generate" in caplog.text
    assert all("lorcana" not in k for k, _ in s3.put) and "catalog/v1/mtg.json" in dict(s3.put)


def test_upload_refuses_a_missing_catalog(fakes, tmp_path):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", client=None)
    s3 = FakeS3()
    assert cli.upload(["mtg"], tmp_path, tmp_path / "c", s3) == ["mtg"]
    assert s3.put == []


def test_upload_refuses_a_missing_index(fakes, tmp_path, caplog):
    cli.generate(["mtg"], tmp_path, tmp_path / "c", client=None, index=False)
    s3 = FakeS3()
    with caplog.at_level("ERROR"):
        assert cli.upload(["mtg"], tmp_path, tmp_path / "c", s3) == ["mtg"]
    assert "mtg.bin" in caplog.text and s3.put == []


def test_upload_refuses_an_index_of_another_catalog(fakes, tmp_path, caplog):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", client=None)
    (tmp_path / catalog_key("lorcana")).write_bytes(b'{"rows":[],"sets":[]}')
    s3 = FakeS3()
    with caplog.at_level("ERROR"):
        assert cli.upload(["lorcana"], tmp_path, tmp_path / "c", s3) == ["lorcana"]
    assert "another catalog" in caplog.text and s3.put == []


def test_upload_refuses_the_index_of_another_game(fakes, tmp_path, caplog):
    cli.generate(["lorcana", "mtg"], tmp_path, tmp_path / "c", client=None)
    (tmp_path / index_key("lorcana")).write_bytes((tmp_path / index_key("mtg")).read_bytes())
    with caplog.at_level("ERROR"):
        assert cli.upload(["lorcana"], tmp_path, tmp_path / "c", FakeS3()) == ["lorcana"]
    assert "index of 'mtg'" in caplog.text


def test_upload_refuses_an_invalid_index(fakes, tmp_path, caplog):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", client=None)
    (tmp_path / index_key("lorcana")).write_bytes(b"not an index")
    with caplog.at_level("ERROR"):
        assert cli.upload(["lorcana"], tmp_path, tmp_path / "c", FakeS3()) == ["lorcana"]
    assert "not a valid index" in caplog.text


def test_upload_exit_code(fakes, tmp_path, monkeypatch):
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", client=None)
    monkeypatch.setattr(cli, "_s3_client", lambda: FakeS3())
    argv = ["upload", "--out", str(tmp_path), "--cache", str(tmp_path / "c")]
    assert cli.main([*argv, "--game", "lorcana"]) == 0
    assert cli.main([*argv, "--game", "lorcana", "--game", "mtg"]) == 1


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
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", client=None)
    calls = []
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: calls.append((args, kwargs)) or FakeS3())

    assert cli.upload(["lorcana"], tmp_path, tmp_path / "c") == []
    assert calls == [(("s3",), {"endpoint_url": "https://s3.example.com", "aws_access_key_id": "key",
                                "aws_secret_access_key": "secret", "region_name": "region"})]


@pytest.mark.parametrize("name", ["CARDLEDGER_S3_ACCESS_KEY_ID", "CARDLEDGER_S3_SECRET_ACCESS_KEY",
                                  "CARDLEDGER_S3_REGION"])
def test_upload_requires_the_credentials_and_region(fakes, tmp_path, monkeypatch, name):
    for key, value in S3_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(name)
    cli.generate(["lorcana"], tmp_path, tmp_path / "c", client=None)

    with pytest.raises(SourceError, match=name):
        cli.upload(["lorcana"], tmp_path, tmp_path / "c")


def test_upload_requires_the_bucket(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CARDLEDGER_S3_BUCKET", raising=False)
    with pytest.raises(SourceError, match="CARDLEDGER_S3_BUCKET"):
        cli.upload(["lorcana"], tmp_path, tmp_path / "c", FakeS3())
    assert cli.main(["upload", "--out", str(tmp_path)]) == 1


def test_upload_cache_defaults_like_generate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(cli, "upload", lambda slugs, out, cache_dir: seen.append((out, cache_dir)) or [])
    assert cli.main(["upload"]) == 0
    assert cli.main(["upload", "--cache", "elsewhere"]) == 0
    assert seen == [(Path("out"), Path(".cache")), (Path("out"), Path("elsewhere"))]


def test_upload_reads_dotenv_from_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CARDLEDGER_S3_BUCKET", raising=False)
    (tmp_path / ".env").write_text("CARDLEDGER_S3_BUCKET=from-dotenv\n")
    seen = []
    monkeypatch.setattr(cli, "upload", lambda *args: seen.append(os.environ.get("CARDLEDGER_S3_BUCKET")) or [])
    assert cli.main(["upload"]) == 0
    assert seen == ["from-dotenv"]


def test_dotenv_does_not_override_the_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CARDLEDGER_S3_BUCKET", "from-env")
    (tmp_path / ".env").write_text("CARDLEDGER_S3_BUCKET=from-dotenv\n")
    seen = []
    monkeypatch.setattr(cli, "upload", lambda *args: seen.append(os.environ.get("CARDLEDGER_S3_BUCKET")) or [])
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
