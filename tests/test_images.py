import hashlib
import json

import cv2
import httpx
import numpy as np
import pytest

from cardledger_database import images
from cardledger_database.catalog import encode
from cardledger_database.model import GameCatalog, Row


def picture(width, height, ext=".png", alpha=False, colour=(10, 120, 200)):
    image = np.zeros((height, width, 4 if alpha else 3), np.uint8)
    image[..., :3] = colour
    if alpha:
        image[..., 3] = 0  # fully transparent
    ok, buf = cv2.imencode(ext, image)
    assert ok
    return buf.tobytes()


PNG_A = picture(600, 840)
PNG_SMALL = picture(200, 280, colour=(50, 50, 50))
JPEG_B = picture(1000, 1400, ".jpg", colour=(200, 20, 20))
PNG_ALPHA = picture(400, 560, alpha=True)


def card(id, **kw):
    fields = dict(kind="card", name="Card", set_code="s", number="1", variant="", rarity="rare", languages=("en",),
                  finishes=("normal",), category="")
    return Row(id, **{**fields, **kw})


def catalog_with(*rows):
    return GameCatalog("x", [], list(rows))


requested_urls: list[str] = []
SERVED = {
    "http://h/a.png": PNG_A,
    "http://h/a-copy.png": PNG_A,
    "http://h/small.png": PNG_SMALL,
    "http://h/b.jpg": JPEG_B,
    "http://h/alpha.png": PNG_ALPHA,
    "http://h/garbage": b"not an image at all",
}


def client(overrides=None):
    def handler(request):
        url = str(request.url)
        requested_urls.append(url)
        if overrides and url in overrides:
            return overrides[url](request)
        if url in SERVED:
            return httpx.Response(200, content=SERVED[url])
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    requested_urls.clear()
    waits: list[float] = []
    monkeypatch.setattr(images, "sleep", waits.append)
    monkeypatch.setattr(images, "_next_slot", {})
    return waits


def read(path):
    return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)


def test_constants_and_paths(tmp_path):
    assert (images.SMALL_WIDTH, images.LARGE_MAX_WIDTH, images.QUALITY) == (300, 745, 85)
    assert images.image_prefix("mtg") == "images/v1/mtg/"
    assert images.bucket_dir(tmp_path) == tmp_path / "bucket"


def test_rows_get_bucket_paths_and_files_exist(tmp_path):
    cat, stats = images.host("x", catalog_with(card("x:s:1", image_source="http://h/a.png")), client(), tmp_path)
    (r,) = cat.rows
    digest = hashlib.sha256(PNG_A).hexdigest()
    assert (r.image, r.image_large) == (f"images/v1/x/{digest}-s.webp", f"images/v1/x/{digest}-l.webp")
    small = cv2.imdecode(np.frombuffer((tmp_path / "bucket" / r.image).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert small.shape[1] == 300 and stats.downloaded == 1
    assert (tmp_path / "bucket" / r.image_large).read_bytes()[8:12] == b"WEBP"
    assert stats == images.ImageStats(downloaded=1, cached=0, failed=0)
    original = tmp_path / "images" / "src" / hashlib.sha1(b"http://h/a.png").hexdigest()
    assert original.read_bytes() == PNG_A


def test_large_is_capped_and_small_is_300(tmp_path):
    cat, _ = images.host("x", catalog_with(card("x:s:1", image_source="http://h/b.jpg")), client(), tmp_path)
    (r,) = cat.rows
    assert read(tmp_path / "bucket" / r.image_large).shape[:2] == (1043, 745)
    assert read(tmp_path / "bucket" / r.image).shape[:2] == (420, 300)


def test_large_is_never_upscaled(tmp_path):
    cat, _ = images.host("x", catalog_with(card("x:s:1", image_source="http://h/small.png")), client(), tmp_path)
    (r,) = cat.rows
    assert read(tmp_path / "bucket" / r.image_large).shape[1] == 200
    assert read(tmp_path / "bucket" / r.image).shape[1] == 200  # narrower than SMALL_WIDTH: not upscaled either


def test_aspect_ratio_is_kept(tmp_path):
    cat, _ = images.host("x", catalog_with(card("x:s:1", image_source="http://h/a.png")), client(), tmp_path)
    (r,) = cat.rows
    assert read(tmp_path / "bucket" / r.image).shape[:2] == (420, 300)
    assert read(tmp_path / "bucket" / r.image_large).shape[:2] == (840, 600)


def test_alpha_is_laid_on_white(tmp_path):
    cat, _ = images.host("x", catalog_with(card("x:s:1", image_source="http://h/alpha.png")), client(), tmp_path)
    small = read(tmp_path / "bucket" / cat.rows[0].image)
    assert small.ndim == 3 and small.shape[2] == 3
    assert small.mean() > 250


def test_cached_original_is_not_downloaded_again(tmp_path):
    cat = catalog_with(card("x:s:1", image_source="http://h/a.png"))
    first, s1 = images.host("x", cat, client(), tmp_path)
    second, s2 = images.host("x", cat, client(), tmp_path)
    assert requested_urls.count("http://h/a.png") == 1
    assert first == second
    assert s2 == images.ImageStats(downloaded=0, cached=1, failed=0)


def test_rows_sharing_a_url_share_one_download(tmp_path):
    cat, stats = images.host("x", catalog_with(card("x:s:1", image_source="http://h/a.png"),
                                               card("x:s:2", image_source="http://h/a.png")), client(), tmp_path)
    assert requested_urls == ["http://h/a.png"]
    assert cat.rows[0].image == cat.rows[1].image != ""
    assert stats == images.ImageStats(downloaded=1, cached=0, failed=0)


def test_same_bytes_share_files(tmp_path):
    cat, stats = images.host("x", catalog_with(card("x:s:1", image_source="http://h/a.png"),
                                               card("x:s:2", image_source="http://h/a-copy.png")), client(), tmp_path)
    a, b = cat.rows
    assert (a.image, a.image_large) == (b.image, b.image_large)
    assert len(list((tmp_path / "bucket" / "images" / "v1" / "x").iterdir())) == 2
    assert stats.downloaded == 2


def test_failed_and_undecodable_images_leave_rows_without_image(tmp_path):
    cat, stats = images.host("x", catalog_with(card("x:s:1", image_source="http://h/404"),
                                               card("x:s:2", image_source="http://h/garbage")), client(), tmp_path)
    assert [r.image for r in cat.rows] == ["", ""] and stats.failed == 2
    assert [r.image_large for r in cat.rows] == ["", ""]
    assert requested_urls.count("http://h/404") == 1  # a 404 is not retried
    src = tmp_path / "images" / "src"
    assert not src.exists() or not any(src.iterdir())  # undecodable bytes are not cached


def test_row_without_source_has_no_image_and_no_request(tmp_path):
    row = card("x:s:1")
    cat, stats = images.host("x", catalog_with(row), client(), tmp_path)
    assert cat.rows == [row] and requested_urls == []
    assert stats == images.ImageStats(0, 0, 0)


def test_retry_on_503_then_success(tmp_path, no_waits):
    calls = []

    def flaky(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "7"})
        return httpx.Response(200, content=PNG_A)

    cat, stats = images.host("x", catalog_with(card("x:s:1", image_source="http://h/flaky.png")),
                             client({"http://h/flaky.png": flaky}), tmp_path)
    assert len(calls) == 2 and cat.rows[0].image != "" and stats.downloaded == 1
    assert 7 in no_waits


def test_retry_after_is_capped_and_attempts_are_limited(tmp_path, no_waits):
    def busy(request):
        return httpx.Response(429, headers={"Retry-After": "3600"})

    cat, stats = images.host("x", catalog_with(card("x:s:1", image_source="http://h/busy.png")),
                             client({"http://h/busy.png": busy}), tmp_path)
    assert requested_urls.count("http://h/busy.png") == 3
    assert cat.rows[0].image == "" and stats.failed == 1
    assert 60 in no_waits and max(no_waits) <= 60


def test_transport_error_is_retried(tmp_path):
    calls = []

    def broken(request):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, content=PNG_A)

    cat, _ = images.host("x", catalog_with(card("x:s:1", image_source="http://h/broken.png")),
                         client({"http://h/broken.png": broken}), tmp_path)
    assert len(calls) == 3 and cat.rows[0].image != ""


def test_referenced_lists_both_sizes():
    cat = catalog_with(card("x:s:1", image="images/v1/x/a-s.webp", image_large="images/v1/x/a-l.webp"),
                       card("x:s:2", image="images/v1/x/a-s.webp", image_large="images/v1/x/a-l.webp"),
                       card("x:s:3"))
    assert images.referenced(encode(cat)) == {"images/v1/x/a-s.webp", "images/v1/x/a-l.webp"}
    assert images.referenced(json.dumps({"rows": [], "sets": []}).encode()) == set()


def test_rerun_leaves_renditions_untouched_and_does_not_decode(tmp_path, monkeypatch):
    cat = catalog_with(card("x:s:1", image_source="http://h/a.png"))
    first, _ = images.host("x", cat, client(), tmp_path)
    paths = [tmp_path / "bucket" / first.rows[0].image, tmp_path / "bucket" / first.rows[0].image_large]
    stamps = [p.stat().st_mtime_ns for p in paths]

    def no_decode(*args):
        raise AssertionError("decoded on a rerun")

    monkeypatch.setattr(images.cv2, "imdecode", no_decode)
    second, stats = images.host("x", cat, client(), tmp_path)
    assert second == first and stats == images.ImageStats(downloaded=0, cached=1, failed=0)
    assert [p.stat().st_mtime_ns for p in paths] == stamps


def test_missing_rendition_is_written_again_from_the_cached_original(tmp_path):
    cat = catalog_with(card("x:s:1", image_source="http://h/a.png"))
    first, _ = images.host("x", cat, client(), tmp_path)
    (tmp_path / "bucket" / first.rows[0].image).unlink()
    second, stats = images.host("x", cat, client(), tmp_path)
    assert second == first and (tmp_path / "bucket" / first.rows[0].image).is_file()
    assert requested_urls.count("http://h/a.png") == 1 and stats.cached == 1


def test_corrupt_cached_original_is_downloaded_again(tmp_path):
    cat = catalog_with(card("x:s:1", image_source="http://h/a.png"))
    original = tmp_path / "images" / "src" / hashlib.sha1(b"http://h/a.png").hexdigest()
    original.parent.mkdir(parents=True)
    original.write_bytes(b"corrupt")
    got, stats = images.host("x", cat, client(), tmp_path)
    assert got.rows[0].image != "" and stats.downloaded == 1
    assert original.read_bytes() == PNG_A


def test_requests_to_one_host_are_spaced(tmp_path, monkeypatch, no_waits):
    monkeypatch.setattr(images, "clock", lambda: 1000.0)  # no time passes: the second request must wait
    images.host("x", catalog_with(card("x:s:1", image_source="http://h/a.png"),
                                  card("x:s:2", image_source="http://h/b.jpg")), client(), tmp_path)
    assert no_waits == [pytest.approx(images.MIN_INTERVAL)]


def test_cancel_drops_the_queued_downloads(tmp_path):
    def handler(request):
        url = str(request.url)
        requested_urls.append(url)
        if url.endswith("/0.png"):
            raise KeyboardInterrupt
        assert images._cancel.wait(5), "the pool was not cancelled"
        return httpx.Response(503)  # would be retried, were it not cancelled

    rows = [card(f"x:s:{i}", image_source=f"http://cancel.test/{i}.png") for i in range(200)]
    with httpx.Client(transport=httpx.MockTransport(handler)) as c, pytest.raises(KeyboardInterrupt):
        images.host("x", catalog_with(*rows), c, tmp_path)
    assert len(requested_urls) <= images.MAX_PARALLEL + 1  # each busy worker made one attempt, the queue none
    assert not images._cancel.is_set()  # the next run works again
