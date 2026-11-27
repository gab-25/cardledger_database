"""The catalog's hosted images: each source image is downloaded once (politely: few at once, a pause between requests
to a host, retries with backoff), kept in the cache, and written as two WebP renditions under the bucket folder,
named after the original's bytes so identical images share their files."""

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import httpx
import numpy as np

from .model import GameCatalog

SMALL_WIDTH = 300
LARGE_MAX_WIDTH = 745
QUALITY = 85  # WebP quality of both renditions
MAX_PARALLEL = 8
MIN_INTERVAL = 0.1  # seconds between two requests to the same host
ATTEMPTS = 3
BACKOFF = (0.5, 1, 2)  # seconds to wait after a failed attempt, when another one follows
MAX_RETRY_AFTER = 60  # a longer Retry-After is capped
WHITE = 255  # under the transparent pixels of a PNG
PROGRESS_EVERY = 1000  # unique URLs between two progress logs

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_next_slot: dict[str, float] = {}
_cancel = threading.Event()  # set while an interrupted download pool winds down: the workers stop early

clock = time.monotonic


def sleep(seconds: float) -> None:  # patched by the tests
    """Waits, but returns at once when the downloads are cancelled."""
    _cancel.wait(seconds)


@dataclass(frozen=True)
class ImageStats:
    """Unique source URLs: downloaded now, found in the cache, or left without an image."""
    downloaded: int
    cached: int
    failed: int


def image_prefix(slug: str) -> str:
    """The bucket folder of a game's images."""
    return f"images/v1/{slug}/"


def bucket_dir(cache_dir: Path) -> Path:
    """The local mirror of the bucket: a file's path under it is its bucket key."""
    return cache_dir / "bucket"


def _source_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / "images" / "src" / hashlib.sha1(url.encode()).hexdigest()


def _write(path: Path, data: bytes) -> None:
    """Writes through a temporary file, so a crash never leaves half a file under the final name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


@contextmanager
def _pool() -> Iterator[ThreadPoolExecutor]:
    """The download workers. On Ctrl+C (or any error) the queued URLs are dropped and the running workers give up
    at their next wait or attempt, so the interruption doesn't wait for the whole batch."""
    pool = ThreadPoolExecutor(MAX_PARALLEL)
    try:
        yield pool
    except BaseException:
        _cancel.set()
        try:
            pool.shutdown(wait=True, cancel_futures=True)
        finally:
            _cancel.clear()
        raise
    pool.shutdown(wait=True)


def _wait_turn(host: str) -> None:
    """Reserves the host's next request slot, then waits for it outside the lock."""
    with _lock:
        now = clock()
        slot = max(now, _next_slot.get(host, now))
        _next_slot[host] = slot + MIN_INTERVAL
    if slot > now:
        sleep(slot - now)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError):
            return None
    return min(max(seconds, 0), MAX_RETRY_AFTER)


def _download(url: str, client: httpx.Client) -> bytes | None:
    """The URL's bytes; None after a 4xx (other than 429) or after ATTEMPTS failed tries."""
    host = urlsplit(url).netloc
    for attempt in range(ATTEMPTS):
        _wait_turn(host)
        if _cancel.is_set():
            return None
        wait = BACKOFF[attempt]
        try:
            response = client.get(url)
            if response.status_code == 200:
                return response.content
            if response.status_code < 500 and response.status_code != 429:
                return None  # 404 and the like will not get better
            if response.status_code in (429, 503):
                wait = _retry_after(response) or wait
        except httpx.HTTPError as exc:
            logger.debug("%s: %s", url, exc)
        if attempt + 1 < ATTEMPTS:
            sleep(wait)
    return None


def _decode(data: bytes) -> np.ndarray | None:
    """The 8-bit BGR image in the bytes, decoded once with IMREAD_UNCHANGED (grey made BGR, 16-bit scaled to 8-bit,
    transparent pixels laid on white); None if they don't decode. EXIF orientation is not applied."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if image.dtype == np.uint16:
        image = (image / 257).round().astype(np.uint8)
    elif image.dtype != np.uint8:
        return None
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        alpha = image[..., 3:].astype(np.float32) / 255
        return (image[..., :3].astype(np.float32) * alpha + WHITE * (1 - alpha)).round().astype(np.uint8)
    if image.shape[2] == 3:
        return image
    return None


def _resize(image: np.ndarray, width: int) -> np.ndarray:
    """The image `width` wide, aspect kept; a narrower image is returned as is (never upscaled)."""
    h, w = image.shape[:2]
    if width >= w:
        return image
    height = max(1, round(h * width / w))
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


def _webp(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".webp", image, [cv2.IMWRITE_WEBP_QUALITY, QUALITY])
    if not ok:
        raise ValueError("WebP encoding failed")
    return buf.tobytes()


def _keys(slug: str, data: bytes) -> tuple[str, str]:
    """The bucket keys of the small and large renditions, named after the sha256 of the original's bytes."""
    digest = hashlib.sha256(data).hexdigest()
    return f"{image_prefix(slug)}{digest}-s.webp", f"{image_prefix(slug)}{digest}-l.webp"


def _missing(keys: tuple[str, str], cache_dir: Path) -> bool:
    return not all((bucket_dir(cache_dir) / key).is_file() for key in keys)


def _write_renditions(keys: tuple[str, str], image: np.ndarray, cache_dir: Path) -> None:
    """Writes the renditions that are missing. The small one is SMALL_WIDTH wide, or as wide as the original when
    that is narrower (never upscaled); the large one is the original's width capped at LARGE_MAX_WIDTH. Both keep
    the aspect ratio and are never cropped."""
    for key, width in zip(keys, (SMALL_WIDTH, LARGE_MAX_WIDTH)):
        path = bucket_dir(cache_dir) / key
        if not path.is_file():
            _write(path, _webp(_resize(image, width)))


def _one(slug: str, url: str, client: httpx.Client, cache_dir: Path) -> tuple[tuple[str, str] | None, bool]:
    """The rendition keys of the URL's image (None if it can't be had) and whether the original came from the
    cache. A cached original whose two renditions exist is not decoded at all; one that must be decoded (a rendition
    is missing) and fails to is deleted and downloaded again. Only an original that decodes is kept in the cache."""
    path = _source_path(cache_dir, url)
    if path.is_file():
        data = path.read_bytes()
        keys = _keys(slug, data)
        if not _missing(keys, cache_dir):
            return keys, True
        image = _decode(data)
        if image is not None:
            _write_renditions(keys, image, cache_dir)
            return keys, True
        path.unlink(missing_ok=True)  # corrupt: download it again
    data = _download(url, client)
    if data is None:
        return None, False
    image = _decode(data)
    if image is None:
        logger.debug("%s: not an image", url)
        return None, False
    _write(path, data)
    keys = _keys(slug, data)
    _write_renditions(keys, image, cache_dir)
    return keys, False


def _safe(slug: str, url: str, client: httpx.Client, cache_dir: Path) -> tuple[tuple[str, str] | None, bool]:
    """`_one`, with any failure on one URL giving no image while the others go on."""
    try:
        return _one(slug, url, client, cache_dir)
    except Exception as exc:  # httpx.InvalidURL, cv2.error, OSError...: skip this image only
        logger.debug("%s: %s: %s", url, type(exc).__name__, exc)
        return None, False


def host(slug: str, catalog: GameCatalog, client: httpx.Client, cache_dir: Path) -> tuple[GameCatalog, ImageStats]:
    """The catalog with each row's `image` / `image_large` set to its renditions' bucket keys ("" when the source
    image can't be had), and the counts of unique source URLs. Rows without `image_source` are left as they are."""
    urls = list(dict.fromkeys(r.image_source for r in catalog.rows if r.image_source))
    results: dict[str, tuple[tuple[str, str] | None, bool]] = {}
    counts = {"downloaded": 0, "cached": 0, "failed": 0}
    with _pool() as pool:
        futures = {pool.submit(_safe, slug, url, client, cache_dir): url for url in urls}
        for future in as_completed(futures):
            keys, cached = results[futures[future]] = future.result()
            counts["failed" if keys is None else "cached" if cached else "downloaded"] += 1
            if len(results) % PROGRESS_EVERY == 0 or len(results) == len(urls):
                logger.info("%s: images %d/%d downloaded=%d cached=%d failed=%d", slug, len(results), len(urls),
                            counts["downloaded"], counts["cached"], counts["failed"])
    rows = []
    for row in catalog.rows:
        if row.image_source:
            keys, _ = results[row.image_source]
            small, large = keys or ("", "")
            row = replace(row, image=small, image_large=large)
        rows.append(row)
    stats = ImageStats(**counts)
    return replace(catalog, rows=rows), stats


def referenced(catalog_bytes: bytes) -> set[str]:
    """Every non-empty `image` and `image_large` of an encoded catalog: the bucket keys it needs."""
    rows = json.loads(catalog_bytes)["rows"]
    return {path for r in rows for path in (r.get("image", ""), r.get("image_large", "")) if path}
