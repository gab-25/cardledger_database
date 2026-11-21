"""MTGJSON's open card data (MIT licence) for Magic: set code, collector number and rarity, keyed by Cardmarket id.

Read from MTGJSON's gzip CSV files (cards and tokens, each with its identifiers file), decompressed while parsed.
MTGJSON has no images: the URLs are Scryfall's, built from each printing's scryfallId. `matcher_input` gives the
same printings as matcher input (ignoring the Cardmarket ids), for the matcher bench.
"""

import csv
import gzip
import io
from datetime import date

import httpx

from .. import http
from ..matcher import Group, Printing
from ..model import Enrichment, SourceCard, SourceError, SourceSet, cardmarket_id, normalize_number

CSV_URL = "https://mtgjson.com/api/v5/csv/{name}.csv.gz"
IMAGE_URL = "https://cards.scryfall.io/{size}/front/{id[0]}/{id[1]}/{id}.jpg"
csv.field_size_limit(1 << 24)  # rules text and printings lists can exceed the default 128 KB


def _rows(client, name):
    response = http.get(client, CSV_URL.format(name=name))
    response.raise_for_status()
    with gzip.open(io.BytesIO(response.content), "rt", encoding="utf-8", newline="") as text:
        yield from csv.DictReader(text)


def _image(scryfall_id, size):
    return IMAGE_URL.format(size=size, id=scryfall_id) if len(scryfall_id) >= 2 else ""


def _identifiers(client, name):
    """{uuid: (Cardmarket id, scryfallId)} of the printings with a Cardmarket id."""
    ids = {}
    for row in _rows(client, name):
        cm_id = cardmarket_id(row["mcmId"])
        if cm_id is not None:
            ids[row["uuid"]] = (cm_id, row["scryfallId"].strip())
    return ids


def _printings(client, name, ids_name, cards):
    """Adds to cards the front face of each printing. Cardmarket often sells a card and its ★/† variant as one
    product: the plain number is kept, otherwise the first printing."""
    ids = _identifiers(client, ids_name)
    for row in _rows(client, name):
        found = ids.get(row["uuid"])
        if found is None or row["side"] not in ("", "a"):
            continue
        cm_id, scryfall_id = found
        number = row["number"]
        if cm_id in cards and (not number.isalnum() or cards[cm_id][0]):
            continue
        cards[cm_id] = (number.isalnum(), SourceCard(
            row["setCode"].lower(), normalize_number(number), row.get("rarity") or "",
            _image(scryfall_id, "small"), _image(scryfall_id, "normal"),
        ))


def _sets(client):
    """The sets, plus their token sets, which have no row of their own: named "<set name> Tokens", like Scryfall."""
    sets, token_sets = {}, {}
    for row in _rows(client, "sets"):
        released = date.fromisoformat(row["releaseDate"]) if row["releaseDate"] else None
        sets[row["code"].lower()] = SourceSet(row["code"].lower(), row["name"], released)
        if token_code := row["tokenSetCode"].strip().lower():
            token_sets[token_code] = SourceSet(token_code, f"{row['name']} Tokens", released)
    return {**token_sets, **sets}


def fetch(client: httpx.Client) -> Enrichment:
    """The Magic enrichment: every MTGJSON card and token with a Cardmarket id, and the sets."""
    cards: dict[int, tuple[bool, SourceCard]] = {}
    try:
        _printings(client, "cards", "cardIdentifiers", cards)
        _printings(client, "tokens", "tokenIdentifiers", cards)
        sets = _sets(client)
    except (httpx.HTTPError, OSError, EOFError, csv.Error, ValueError, KeyError, TypeError) as exc:
        raise SourceError(f"MTGJSON: {type(exc).__name__}: {exc}") from exc
    return Enrichment(cards={cm_id: card for cm_id, (_, card) in cards.items()}, sets=sets)


def matcher_input(client: httpx.Client) -> tuple[list[Group], list[Printing]]:
    """Groups (one per set code, ids by sorted code) and printings (front faces of cards and tokens, the name before
    " // "), without images: the bench judges set codes and numbers only."""
    try:
        sets = _sets(client)
        rows = [row for name in ("cards", "tokens") for row in _rows(client, name) if row["side"] in ("", "a")]
    except (httpx.HTTPError, OSError, EOFError, csv.Error, ValueError, KeyError, TypeError) as exc:
        raise SourceError(f"MTGJSON: {type(exc).__name__}: {exc}") from exc
    groups = []
    ids = {}
    for code in sorted({row["setCode"] for row in rows}):
        known = sets.get(code.lower())
        ids[code] = len(groups) + 1
        groups.append(Group(ids[code], known.name if known else code, code, known.released_at if known else None))
    printings = [
        Printing(i, ids[row["setCode"]], row["name"].split(" // ")[0], row["number"], row.get("rarity") or "", "", "")
        for i, row in enumerate(rows, start=1)
    ]
    return groups, printings
