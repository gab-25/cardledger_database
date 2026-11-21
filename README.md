# cardledger_database

Generator of the CardLedger card catalog for six trading card games (Magic, Pokémon, Lorcana, One Piece, Yu-Gi-Oh!,
Riftbound), built from public sources. It is run by hand every now and then: `generate` writes the files to a local
folder, you look at them, `upload` copies them to the S3 bucket. The generated files are private (they live in the
bucket) and are never committed to this repository; only the code is.

## Sources and licences

Every product keeps its Cardmarket product id; the sources add set codes, collector numbers, rarities and images.
The catalog stores only image URLs, served by each source. MTGJSON has no images: the Magic URLs point to
[Scryfall](https://scryfall.com)'s image server, built from MTGJSON's Scryfall ids.

| Game | Source | Terms |
| --- | --- | --- |
| Magic | [MTGJSON](https://mtgjson.com) CSV files | MIT |
| Pokémon | [TCGdex](https://tcgdex.dev) (EN + JA) | MIT |
| Lorcana | [LorcanaJSON](https://lorcanajson.org) | Fan project, used under Ravensburger's Community Code Policy |
| One Piece | [punk-records](https://github.com/buhbbl/punk-records) (English, from the official card list) | Pipeline AGPL-3.0; card data © Bandai/Shueisha |
| Yu-Gi-Oh!, Riftbound | [TCGCSV](https://tcgcsv.com) (TCGplayer data) | No licence stated |

The Cardmarket product lists are the public files published by Cardmarket. One Piece is joined on the card code printed in the Cardmarket name (images only where the variant is unambiguous). The TCGCSV games are matched to Cardmarket products by name; after changing the matcher, check it with
`uv run python scripts/run_bench.py` (it measures the wrong matches on Magic, where MTGJSON gives the truth, and exits
with 1 above 1%). No prices are included.

## Output (format v1)

`<slug>.jsonl.gz` under `catalog/v1/`, one per game (`mtg`, `pokemon`, `lorcana`, `onepiece`, `yugioh`, `riftbound`),
the same path in the output folder and in the bucket: gzip-compressed JSON lines, first the `expansion` lines (`code`,
`name`, `released_at`, `language`), then the `product` lines (`id_product`, `name`, `expansion_code`, `number`,
`rarity`, `image_url`, `image_large_url`, `category_id`, `category_name`).

## Corrections

Wrong or missing images, rarities and set codes are fixed by hand in `corrections/<slug>.toml`, applied on every run.
See [corrections/README.md](corrections/README.md).

## Running

```bash
uv sync
uv run pytest -q
uv run cardledger-db generate                # all games into out/catalog/v1/
uv run cardledger-db generate --game pokemon # only some games
uv run cardledger-db upload                  # out/catalog/v1/*.jsonl.gz to the bucket
uv run cardledger-db upload --game pokemon   # only some games
```

Run it from the repository root (`corrections/` is read relative to the working directory). `generate` logs, per game,
the products, the products enriched by the source, the expansions and the share with an image: compare them with the
previous run before uploading. A game that fails keeps its previous file in `out/`, and the command exits with 1.
`upload` refuses to start if a requested game has no file. The TCGdex cards are cached in `.cache/tcgdex/` (`--cache`
to change it): keep that folder between runs, without it every Pokémon card is downloaded again.

## Environment variables

Only `upload` uses them:

- `CARDLEDGER_S3_BUCKET`: the bucket (required, no default)
- `CARDLEDGER_S3_ENDPOINT`: endpoint of an S3-compatible service (optional, empty means AWS)
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`: the credentials
- `AWS_DEFAULT_REGION`: required (use `auto` for Cloudflare R2)

The credentials need only `s3:PutObject` on the bucket.

## CI

`tests.yml` runs the tests on every push and pull request.
