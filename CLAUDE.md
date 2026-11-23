# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CLI (`cardledger-db`) that builds the CardLedger card catalog for six games (mtg, pokemon, lorcana, onepiece,
yugioh, riftbound) from public sources. It is run by hand: `generate` writes `out/catalog/v1/<slug>.json` and its recognition index
`out/vision/v1/<slug>.bin`, a human checks the logged counts, `upload` copies the files to S3. Generated files are never committed. README.md is the
user-facing reference (sources and licences, output format, env vars).

## Commands

Python 3.14, managed with uv. Run from the repository root (`corrections/` is read relative to the working directory).

```bash
uv sync
uv run pytest -q                                   # all tests (what CI runs)
uv run pytest tests/test_matcher.py -q             # one file
uv run pytest tests/test_matcher.py::test_name -q  # one test
uv run cardledger-db generate --game pokemon       # real network run; --out / --cache change folders
uv run cardledger-db index --game pokemon          # only the index, from the catalog already in out/
uv run cardledger-db upload --game pokemon         # needs CARDLEDGER_S3_* (env or .env)
```

There is no linter or formatter configured.

## Architecture

Per game, `cli.generate_game` runs a fixed pipeline:

1. **Cardmarket** (`sources/cardmarket.py`): the public product lists (singles + nonsingles) are the backbone.
   Every catalog row is a Cardmarket product and keeps its `id_product`.
2. **Enrichment** from the game's source (`Game.source` in `model.GAMES` selects it in `cli._enrichment`). Every
   source returns an `Enrichment`: `cards` keyed by Cardmarket `id_product`, `sets` keyed by set code, and optionally
   `expansions` (Cardmarket idExpansion → set code). Sources join to Cardmarket differently:
   - mtgjson, tcgdex, lorcanajson, openrift (Riftbound): the source carries Cardmarket ids directly.
   - punkrecords (One Piece): joined on the card code printed in the Cardmarket name (`model.code_of`).
   - yamlyugi: returns `Group`/`Printing` lists and goes through `matcher.match`, which links by normalized
     name plus an expansion→group vote. Its rule: prefer no match over a doubtful one (a wrong match shows the
     wrong image); keep that bias when changing thresholds.
3. **Corrections** (`corrections.py`): `corrections/<slug>.toml` hand-fixes applied after the automatic match. Strict
   schema: unknown keys/wrong types fail the run; stale ids only warn. Format in `corrections/README.md`.
4. **Assemble** (`catalog.assemble`): unmatched products get their expansion's majority set code; if none and
   `Game.keep_unmatched` is true they get a stand-in `cmx<idExpansion>` expansion, otherwise they are dropped
   (`skipped`). `catalog.encode` is deterministic (sorted lists and keys): same catalog, same bytes.

5. **Index** (`cli._index`): `cardledger_vision.build_index` (a git dependency pinned to a release tag) hashes the catalog's
   images into the recognition index, cached in `.cache/vision/`. It is built before anything is written, so a game
   whose source or index fails keeps its previous pair of files; `upload` checks each index's game and catalog
   sha256 before uploading anything. The hash, format and thresholds belong to cardledger_vision: change them there.

A game that fails logs and keeps its previous output files; `SourceError` is the expected "source unavailable / bad
shape" error (logged without traceback). HTTP goes through `http.get` (3 attempts on 5xx/transport errors); TCGdex
cards are cached in `.cache/tcgdex/`.

Tests never hit the network: sources are tested with `httpx.MockTransport` clients, the CLI tests fake `build_index`, and `tests/conftest.py`
autouse-patches `http.sleep` so retries don't wait.

## Constraints

- `id_product` is the key the CardLedger app uses; changing or dropping it is breaking. Expansion codes are only for
  grouping and may change between runs.
- Data sources must be openly licensed / clear provenance; flag non-open ones. Don't reintroduce TCGCSV.
- Superpowers working files (`docs/superpowers/`, `.superpowers/`) are gitignored and must never be committed.
