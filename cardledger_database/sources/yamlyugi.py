"""yaml-yugi's Yu-Gi-Oh! card database (pipeline AGPL-3.0, card data © Konami): the English sets and printings, as
matcher input. Images are Yugipedia's files, only for cards with a single artwork (a printing's artwork is unknown)."""

import hashlib
from collections import Counter, defaultdict

import httpx

from .. import http
from ..matcher import Group, Printing
from ..model import SourceError

URL = "https://dawnbrandbots.github.io/yaml-yugi/cards.json"
IMAGE_URL = "https://img.yugipedia.com/{h[0]}/{h[0]}{h[1]}/{file}"


def image_url(file: str) -> str:
    """The Yugipedia URL of an image file (MediaWiki's md5 path)."""
    return IMAGE_URL.format(h=hashlib.md5(file.encode()).hexdigest(), file=file)


def _english(card: dict) -> tuple[str, list[dict]]:
    name = (card.get("name") or {}).get("en") or ""
    return name, ((card.get("sets") or {}).get("en") or []) if name else []


def parse(cards: list) -> tuple[list[Group], list[Printing]]:
    """Groups (one per English set name, ids by sorted name) and printings (one per card, set and rarity)."""
    prefixes: dict[str, Counter] = defaultdict(Counter)
    for card in cards:
        for entry in _english(card)[1]:
            prefixes[entry["set_name"]][entry["set_number"].split("-")[0]] += 1
    ids = {name: i for i, name in enumerate(sorted(prefixes), start=1)}
    groups = [Group(ids[name], name, min(c.items(), key=lambda kv: (-kv[1], kv[0]))[0], None)
              for name, c in sorted(prefixes.items())]
    printings: list[Printing] = []
    for card in cards:
        name, entries = _english(card)
        images = card.get("images") or []
        image = image_url(images[0]["image"]) if len(images) == 1 and images[0].get("image") else ""
        for entry in entries:
            for rarity in entry.get("rarities") or [""]:
                printings.append(Printing(len(printings) + 1, ids[entry["set_name"]], name, entry["set_number"],
                                          rarity, image, image))
    return groups, printings


def fetch(client: httpx.Client) -> tuple[list[Group], list[Printing]]:
    try:
        response = http.get(client, URL)
        response.raise_for_status()
        return parse(response.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise SourceError(f"yaml-yugi: {type(exc).__name__}: {exc}") from exc
