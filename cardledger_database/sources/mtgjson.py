"""MTGJSON's open card data (MIT licence) for Magic: set code, collector number and rarity, keyed by Cardmarket id.

Read from MTGJSON's gzip CSV files (cards and tokens, each with its identifiers file), decompressed while parsed.
MTGJSON has no images: the URLs are Scryfall's, built from each printing's scryfallId. Also returns the TCGplayer
ids of each card, the truth for the matcher bench.
"""

import csv
import gzip
import io
from datetime import date

import httpx

from .. import http
from ..model import Enrichment, SourceCard, SourceError, SourceSet, cardmarket_id, normalize_number

CSV_URL = "https://mtgjson.com/api/v5/csv/{name}.csv.gz"
IMAGE_URL = "https://cards.scryfall.io/{size}/front/{id[0]}/{id[1]}/{id}.jpg"
TCGPLAYER_COLUMNS = ("tcgplayerProductId", "tcgplayerEtchedProductId")
csv.field_size_limit(1 << 24)  # rules text and printings lists can exceed the default 128 KB


def _rows(client, name):
    response = http.get(client, CSV_URL.format(name=name))
    response.raise_for_status()
    with gzip.open(io.BytesIO(response.content), "rt", encoding="utf-8", newline="") as text:
        yield from csv.DictReader(text)


def _image(scryfall_id, size):
    return IMAGE_URL.format(size=size, id=scryfall_id) if len(scryfall_id) >= 2 else ""


def _identifiers(client, name, truth):
    """{uuid: (Cardmarket id, scryfallId)} of the printings with a Cardmarket id; adds their TCGplayer ids to truth."""
    ids = {}
    for row in _rows(client, name):
        cm_id = cardmarket_id(row["mcmId"])
        if cm_id is None:
            continue
        ids[row["uuid"]] = (cm_id, row["scryfallId"].strip())
        tcg = {cardmarket_id(row[k]) for k in TCGPLAYER_COLUMNS} - {None}
        if tcg:
            truth.setdefault(cm_id, set()).update(tcg)
    return ids


def _printings(client, name, ids_name, cards, truth):
    """Adds to cards the front face of each printing. Cardmarket often sells a card and its ★/† variant as one
    product: the plain number is kept, otherwise the first printing."""
    ids = _identifiers(client, ids_name, truth)
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


def fetch(client: httpx.Client) -> tuple[Enrichment, dict[int, set[int]]]:
    """The Magic enrichment and {Cardmarket id: {TCGplayer ids}} of every MTGJSON card and token."""
    cards: dict[int, tuple[bool, SourceCard]] = {}
    truth: dict[int, set[int]] = {}
    try:
        _printings(client, "cards", "cardIdentifiers", cards, truth)
        _printings(client, "tokens", "tokenIdentifiers", cards, truth)
        sets = _sets(client)
    except (httpx.HTTPError, OSError, EOFError, csv.Error, ValueError, KeyError, TypeError) as exc:
        raise SourceError(f"MTGJSON: {type(exc).__name__}: {exc}") from exc
    return Enrichment(cards={cm_id: card for cm_id, (_, card) in cards.items()}, sets=sets), truth
