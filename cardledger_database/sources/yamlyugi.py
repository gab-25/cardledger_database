"""yaml-yugi's Yu-Gi-Oh! card database (pipeline AGPL-3.0, card data © Konami): the English and Japanese (OCG) sets
and printings, as matcher input. yaml-yugi names Japanese sets and cards in English too, so both match Cardmarket's
English product names. Images are Yugipedia's files, only for cards with a single artwork (a printing's artwork is
unknown)."""

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


LANGUAGES = {"en": "", "ja": "ja"}  # yaml-yugi set language -> Group.language


def _entries(card: dict, lang: str) -> tuple[str, list[dict]]:
    """The card's English name and its sets in `lang` (none without an English name)."""
    name = (card.get("name") or {}).get("en") or ""
    return name, ((card.get("sets") or {}).get(lang) or []) if name else []


def parse(cards: list) -> tuple[list[Group], list[Printing]]:
    """Groups (one per set name and language: the English ones first, each language by sorted name) and printings
    (one per card, set and rarity). A Japanese group's abbreviation is its prefix plus "-JP" ("SAST-JP"), apart
    from its English twin's."""
    groups: list[Group] = []
    ids: dict[tuple[str, str], int] = {}
    for lang, language in LANGUAGES.items():
        prefixes: dict[str, Counter] = defaultdict(Counter)
        for card in cards:
            for entry in _entries(card, lang)[1]:
                prefixes[entry["set_name"]][entry["set_number"].split("-")[0]] += 1
        for name, counter in sorted(prefixes.items()):
            ids[(lang, name)] = len(groups) + 1
            abbreviation = min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            groups.append(Group(ids[(lang, name)], name, f"{abbreviation}-JP" if language == "ja" else abbreviation,
                                None, language))
    printings: list[Printing] = []
    for lang in LANGUAGES:
        for card in cards:
            name, entries = _entries(card, lang)
            images = card.get("images") or []
            image = image_url(images[0]["image"]) if len(images) == 1 and images[0].get("image") else ""
            for entry in entries:
                for rarity in entry.get("rarities") or [""]:
                    printings.append(Printing(len(printings) + 1, ids[(lang, entry["set_name"])], name,
                                              entry["set_number"], rarity, image, image))
    return groups, printings


def fetch(client: httpx.Client) -> tuple[list[Group], list[Printing]]:
    try:
        response = http.get(client, URL)
        response.raise_for_status()
        return parse(response.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise SourceError(f"yaml-yugi: {type(exc).__name__}: {exc}") from exc
