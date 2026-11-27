# cardledger_database

Generator of the CardLedger card catalog for six trading card games (Magic, Pokémon, Lorcana, One Piece, Yu-Gi-Oh!,
Riftbound), built from public sources, of the catalog's images and of each catalog's recognition index. It is run by
hand every now and then: `generate` writes the files to local folders, you look at them, `upload` copies them to the S3
bucket. The generated files are private (they live in the bucket) and are never committed to this repository; only the
code is.

## Sources and licences

Each game is built from one dataset: one row per printing (set + collector number + variant), with the languages and
finishes it exists in as attributes. The catalog holds no market ids and no prices.

| Game | Source | Terms |
| --- | --- | --- |
| Magic | [Scryfall](https://scryfall.com) `default_cards` bulk file and sets | Not openly licensed: accepted exception (below) |
| Pokémon | [TCGdex](https://tcgdex.dev) (EN + JA) | MIT |
| Lorcana | [LorcanaJSON](https://lorcanajson.org) (EN, FR, DE, IT) | Open fan project over Ravensburger's data, used under its Community Code Policy |
| One Piece | [punk-records](https://github.com/buhbbl/punk-records) (EN + JA, from the official card lists) | Pipeline AGPL-3.0; card data © Bandai/Shueisha |
| Yu-Gi-Oh! | [YGOPRODeck](https://ygoprodeck.com/api-guide/) API v7 (TCG printings) | Not openly licensed: accepted exception (below) |
| Riftbound | [OpenRift](https://openrift.app/developers) API | Pipeline AGPL-3.0; card data and images © Riot Games |

Scryfall and YGOPRODeck are deliberate exceptions to the open-sources rule, with their terms kept:

- Scryfall: its data is never put behind a paywall; its images are never cropped, covered, distorted or colour-shifted
  (only resized, aspect ratio kept); the bulk file is used instead of per-card calls.
- YGOPRODeck: at most 20 requests a second (more blocks the IP for an hour); its data and images are downloaded once
  and hosted by us, never hotlinked. A run makes two API requests at most (both cached for a day).

Every image is hosted on our bucket, which makes CardLedger their distributor: all images are © their publishers.
Riftbound's is the weakest case: Riot's policy allows only images taken from the Riot API, and these come from
OpenRift.

## Output

`<slug>.json` under `catalog/v1/`, one per game (`mtg`, `pokemon`, `lorcana`, `onepiece`, `yugioh`, `riftbound`),
the same path in the output folder and in the bucket: one uncompressed JSON object, `{"rows": [...], "sets": [...]}`,
keys and lists sorted (the same catalog always gives the same bytes).

- set: `code`, `name`, `released_at` (ISO date or null), `region` (`""`, or `"ja"` for a Japanese set).
- row: `id`, `kind` (`"card"` or `"sealed"`), `name`, `set_code`, `number`, `variant`, `rarity`, `languages` (ISO
  codes, empty when unknown), `finishes`, `category` (`""` for cards; the sealed category), `image`, `image_large`.

A row's `id` is `<game>:<set code>:<number>[:<variant>]` (`mtg:lea:161`, `onepiece:op01:OP01-001:p1`), sealed products
`<game>:sealed:<key>`. It is built from the source before corrections and is the key the CardLedger app uses: a change
is breaking. Rows of the source with the same id are merged into one (languages and finishes united); the log counts
them. `image` and `image_large` are paths relative to the bucket root (`""` when the row has no image); the base URL
is configured in cardledger_core.

### Images

Every source image is downloaded once (8 at once, a pause between requests to a host, retries on 5xx/429) into
`.cache/images/src/` and written as two WebP renditions, `<sha256>-s.webp` (300 px wide) and `<sha256>-l.webp` (at
most 745 px wide, never upscaled), under `.cache/bucket/images/v1/<slug>/`, the local mirror of the bucket. The name
is the sha256 of the original's bytes: a path changes only when the source image does, and rows sharing an image share
its files. An image that can't be downloaded or decoded leaves its row without one. Images no longer referenced stay in
the bucket.

### Recognition index

Next to each catalog, `generate` writes `vision/v1/<slug>.bin`, the index that recognizes the game's single cards in
photos, built by [cardledger_vision](https://github.com/gab-25/cardledger_vision) (`build_index`) from the renditions in
`.cache/bucket/`. Its header holds the sha256 of the catalog it was built from. cardledger_vision is pinned to a
release tag in `pyproject.toml` (`[tool.uv.sources]`): when the hash, the index format or the thresholds change there,
move the `tag` to the new release, run `uv lock --upgrade-package cardledger-vision` and then `index`.

A game marked `confirm_only` in `model.GAMES` gets an index that never accepts a card without confirmation. For now
every game is: set `confirm_only=False` for a game once cardledger_vision's `bench` finds at most 2% `wrong_certain`
for it.

## Sealed products and corrections

No source lists sealed products: they are written by hand in `sealed/<slug>.toml`, see
[sealed/README.md](sealed/README.md). Wrong names, numbers, rarities, images and renamed set codes are fixed by hand
in `corrections/<slug>.toml`, by row id and set code, applied on every run: see
[corrections/README.md](corrections/README.md).

## Running

```bash
uv sync
uv run pytest -q
uv run cardledger-db generate                # all games: out/catalog/v1/*.json, out/vision/v1/*.bin, .cache/bucket/
uv run cardledger-db generate --game pokemon # only some games
uv run cardledger-db generate --no-index     # only the catalogs and images (quicker, to look at them; upload then refuses)
uv run cardledger-db index                   # only the indexes, from the catalogs already in out/
uv run cardledger-db upload                  # every catalog with its images and index to the bucket
uv run cardledger-db upload --game pokemon   # only some games
```

Run it from the repository root (`corrections/` and `sealed/` are read relative to the working directory); `--out`
and `--cache` change the output and cache folders (`upload --cache` must be the folder `generate` used).

`generate` logs, per game, the rows (cards and sealed), the sets, the rows merged, the share with an image and the
images downloaded and failed: compare them with the previous run before uploading. A game that fails (its source or
its index) keeps its previous files in `out/`, and the command exits with 1.

`upload`, per game: checks that its index was built from its catalog and that every image the catalog references is in
`.cache/bucket/`, uploads the images the bucket lacks (`Cache-Control: public, max-age=31536000, immutable`), then the
index, then the catalog last, so a published catalog never points to a missing image. A game that fails a check
uploads nothing and the command exits with 1. Making `images/` publicly readable (bucket policy or CDN) is set up
outside this repository.

Keep `.cache/` between runs: it holds the source files (`scryfall/`, `tcgdex/`, `ygoprodeck/`, `punkrecords/`), the
original images (`images/src/`), the renditions (`bucket/`) and the index hashes (`vision/`). Without it every image is
downloaded again.

## Environment variables

Only `upload` uses them:

- `CARDLEDGER_S3_BUCKET`: the bucket (required)
- `CARDLEDGER_S3_ENDPOINT`: endpoint of the S3-compatible service (optional, empty means AWS)
- `CARDLEDGER_S3_ACCESS_KEY_ID`, `CARDLEDGER_S3_SECRET_ACCESS_KEY`: the credentials (required)
- `CARDLEDGER_S3_REGION`: the bucket's region (required)

The credentials need `s3:PutObject` and `s3:ListBucket` on the bucket. `upload` reads them from the environment or from
the `.env` file in the working directory (the environment wins).

## CI

`tests.yml` runs the tests on every push to `main`.
