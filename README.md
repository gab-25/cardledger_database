# cardledger_database

Nightly generator of the CardLedger card catalog for six trading card games (Magic, Pokémon, Lorcana, One Piece,
Yu-Gi-Oh!, Riftbound), built from public sources and published as files to S3. The generated files are private (they
live in the S3 bucket) and are never committed to this repository; only the code is.

## Sources and licences

Every product keeps its Cardmarket product id; the sources add set codes, collector numbers, rarities and images.

| Game | Source | Terms |
| --- | --- | --- |
| Magic | [Scryfall](https://scryfall.com) bulk data | [Scryfall data guidelines](https://scryfall.com/docs/api) (attribution, no paywalling the data) |
| Pokémon | [TCGdex](https://tcgdex.dev) (EN + JA) | MIT |
| Lorcana | [LorcanaJSON](https://lorcanajson.org) | Fan project, used under Ravensburger's Community Code Policy |
| One Piece, Yu-Gi-Oh!, Riftbound | [TCGCSV](https://tcgcsv.com) (TCGplayer data) | No licence stated |

The Cardmarket product lists are the public files published by Cardmarket. The TCGCSV games are matched to Cardmarket
products by name; the matcher is gated by a Magic bench (at most 1% wrong matches), and above that threshold those games
are not published. A game with less than half the products of the previous publication is not published either.
No prices are included.

## Output (format v1)

Under `catalog/v1/` in the bucket:

- `<slug>.jsonl.gz`, one per game (`mtg`, `pokemon`, `lorcana`, `onepiece`, `yugioh`, `riftbound`): gzip-compressed JSON
  lines, first the `expansion` lines (`code`, `name`, `released_at`, `language`), then the `product` lines
  (`id_product`, `name`, `expansion_code`, `number`, `rarity`, `image_url`, `image_large_url`, `category_id`,
  `category_name`).
- `manifest.json`: `format_version`, `generated_at` and, per game, `key`, `sha256`, `products`, `expansions` and
  `generated_at`.

The TCGdex cache is kept under `cache/tcgdex/`.

## Corrections

Wrong or missing images, rarities and set codes are fixed by hand in `corrections/<slug>.toml`, applied on every run.
See [corrections/README.md](corrections/README.md).

## Running locally

```bash
uv sync
uv run pytest -q
uv run cardledger-db generate --out out            # all games into a local folder
uv run cardledger-db generate --game mtg --out out # only some games
```

Run it from the repository root (`corrections/` is read relative to the working directory). Without `--out` the files
and the cache go to S3. The command exits with 1 if any game failed.

## Environment variables

S3 settings come only from the environment (the workflow reads them from repository secrets):

- `CARDLEDGER_S3_BUCKET`: the bucket (required without `--out`, no default)
- `CARDLEDGER_S3_ENDPOINT`: endpoint of an S3-compatible service (optional, empty means AWS)
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`

## CI

`tests.yml` runs the tests on every push and pull request. `generate.yml` runs `cardledger-db generate` every night at
03:30 UTC (and on demand with `workflow_dispatch`).
