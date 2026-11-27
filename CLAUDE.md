# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CLI (`cardledger-db`) that builds the CardLedger card catalog for six games (mtg, pokemon, lorcana, onepiece,
yugioh, riftbound) from public sources. It is run by hand: `generate` writes `out/catalog/v1/<slug>.json`, its recognition index
`out/vision/v1/<slug>.bin` and its images under `.cache/bucket/`, a human checks the logged counts, `upload` copies the files to S3. Generated files are never committed. README.md is the
user-facing reference (sources and licences, output format, env vars).

## Commands

Python 3.14, managed with uv. Run from the repository root (`corrections/` and `sealed/` are read relative to the working directory).

```bash
uv sync
uv run pytest -q                                   # all tests (what CI runs)
uv run pytest tests/test_catalog.py -q             # one file
uv run pytest tests/test_catalog.py::test_name -q  # one test
uv run cardledger-db generate --game pokemon       # real network run; --out / --cache change folders
uv run cardledger-db index --game pokemon          # only the index, from the catalog already in out/
uv run cardledger-db upload --game pokemon         # needs CARDLEDGER_S3_* (env or .env); --cache as generate
```

There is no linter or formatter configured.

## Architecture

Per game, `cli.generate_game` runs a fixed pipeline:

1. **Source** (`Game.source` in `model.GAMES` selects it in `cli._source`): scryfall (MTG), tcgdex (Pokémon),
   lorcanajson, punkrecords (One Piece), ygoprodeck (Yu-Gi-Oh!), openrift (Riftbound). Each returns `SourceData`:
   one `Row` per printing (set + number + variant; languages and finishes are attributes) and the `CardSet`s. Row ids
   (`model.row_id`: `<game>:<set code>:<number>[:<variant>]`) are built here from the source's own data, never from
   source-internal ids, and set codes are a pure function of the set's own data. Each row carries `image_source`.
2. **Corrections** (`corrections.py`): `corrections/<slug>.toml` hand-fixes `[[card]]` by id (never changes the id) and
   `[[set]]` by code (`source_code` pins a renamed set code). Strict schema: unknown keys/sections or wrong types fail
   the run; stale ids only warn. Format in `corrections/README.md`.
3. **Assemble** (`catalog.assemble`): adds the hand-written sealed products (`sealed.load`, `sealed/<slug>.toml`, id
   `<game>:sealed:<key>`), merges rows with the same id (counted in `merged`, warned when they differ beyond
   languages/finishes). `catalog.encode` is deterministic (sorted lists and keys): same catalog, same bytes.
4. **Images** (`images.host`): each `image_source` is downloaded once into `<cache>/images/src/` and written as two
   WebP renditions in `<cache>/bucket/images/v1/<slug>/<sha256 of the original>-{s,l}.webp`; rows get these
   bucket-relative paths. A failed image leaves the row without one; the run goes on.
5. **Index** (`cli._index`): `cardledger_vision.build_index` (a git dependency pinned to a release tag) hashes the
   renditions in `<cache>/bucket/` into the recognition index, hashes cached in `<cache>/vision/`. It is built before
   anything is written, so a game whose source or index fails keeps its previous pair of files. The hash, format and
   thresholds belong to cardledger_vision: change them there.

`upload`, per game: checks the index's game and catalog sha256 and that every referenced image is in `<cache>/bucket/`
(else that game uploads nothing), uploads the images the bucket lacks, then the index, then the catalog last.

A game that fails logs and keeps its previous output files; `SourceError` is the expected "source unavailable / bad
shape" error (logged without traceback). HTTP goes through `http.get` (3 attempts on 5xx/transport errors); source
files are cached under `<cache>/` (`scryfall/`, `tcgdex/`, `ygoprodeck/`, `punkrecords/`).

Tests never hit the network: sources are tested with `httpx.MockTransport` clients, the CLI tests fake the sources,
the image downloads and `build_index`, and `tests/conftest.py` autouse-patches `http.sleep` so retries don't wait.

## Constraints

- A row's `id` is the key the CardLedger app uses; changing it is breaking (a set code rename is pinned with a
  `[[set]]` correction). An image path changes only when the source image's bytes do.
- Data sources must be openly licensed / clear provenance; flag non-open ones (Scryfall and YGOPRODeck are accepted
  exceptions, their terms in README.md). Never read Cardmarket. Don't reintroduce TCGCSV.
- Superpowers working files (`docs/superpowers/`, `.superpowers/`) are gitignored and must never be committed.
