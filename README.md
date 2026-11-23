# cardledger_database

Generator of the CardLedger card catalog for six trading card games (Magic, Pokémon, Lorcana, One Piece, Yu-Gi-Oh!,
Riftbound), built from public sources, and of each catalog's recognition index. It is run by hand every now and then:
`generate` writes the files to a local folder, you look at them, `upload` copies them to the S3 bucket. The generated files are private (they live in the
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
| Yu-Gi-Oh! | [yaml-yugi](https://github.com/DawnbrandBots/yaml-yugi) (English and Japanese sets), images from [Yugipedia](https://yugipedia.com) | Pipeline AGPL-3.0; card data © Konami |
| Riftbound | [OpenRift](https://openrift.app/developers) API, images served by OpenRift | Pipeline AGPL-3.0; card data and images © Riot Games |

The Cardmarket product lists are the public files published by Cardmarket. One Piece is joined on the card code printed in the Cardmarket name. When one code has several Cardmarket products in an expansion (base and parallels), the base is the oldest product and must also be the cheapest one in Cardmarket's price guide; otherwise those products get no image. OpenRift gives each Riftbound printing its Cardmarket product id; when several printings (languages, foil) share one product, the English plain one is used. TCGdex links each Pokémon card, and each of its variants (stamps, special foils), to a Cardmarket product; a variant shows its card's number and image. The card's other versions (same name, same Cardmarket expansion) get its image when all versions with an image agree. Yu-Gi-Oh! is matched to Cardmarket products by name (a repeated name still gets its
number, and its image and rarity when all its printings agree); when reprint sets hold the same cards, the name of the expansion, taken from its sealed products, decides, and without a clear winner the products stay unmatched. Expansions whose sealed products are marked "(OCG)" or "(Japanese)" are matched to yaml-yugi's Japanese sets only; unmarked ones to the English sets first. No prices are included.

## Output (format v1)

`<slug>.json` under `catalog/v1/`, one per game (`mtg`, `pokemon`, `lorcana`, `onepiece`, `yugioh`, `riftbound`),
the same path in the output folder and in the bucket: one uncompressed JSON object,
`{"expansions": [...], "products": [...]}`. Each expansion has `code`, `name`, `released_at`, `language`; each
product has `id_product`, `name`, `expansion_code`, `number`, `rarity`, `image_url`, `image_large_url`,
`category_id`, `category_name`.

### Recognition index

Next to each catalog, `generate` writes `vision/v1/<slug>.bin`, the index that recognizes the game's single cards in
photos, built by [cardledger_vision](https://github.com/gab-25/cardledger_vision) (`build_index`) from the catalog's
images. Its header holds the sha256 of the catalog it was built from. cardledger_vision is pinned to a release tag in
`pyproject.toml` (`[tool.uv.sources]`): when the hash, the index format or the thresholds change there, move the
`tag` to the new release, run `uv lock --upgrade-package cardledger-vision` and then `index`.

A game marked `confirm_only` in `model.GAMES` gets an index that never accepts a card without confirmation. For now every game is: set `confirm_only=False` for a game once cardledger_vision's `bench` finds at
most 2% `wrong_certain` for it.

## Corrections

Wrong or missing images, rarities and set codes are fixed by hand in `corrections/<slug>.toml`, applied on every run.
See [corrections/README.md](corrections/README.md).

## Running

```bash
uv sync
uv run pytest -q
uv run cardledger-db generate                # all games: out/catalog/v1/*.json and out/vision/v1/*.bin
uv run cardledger-db generate --game pokemon # only some games
uv run cardledger-db generate --no-index     # only the catalogs (quicker, to look at them; upload then refuses)
uv run cardledger-db index                   # only the indexes, from the catalogs already in out/
uv run cardledger-db upload                  # every catalog and its index to the bucket
uv run cardledger-db upload --game pokemon   # only some games
```

Run it from the repository root (`corrections/` is read relative to the working directory); `--out` and `--cache`
change the output and cache folders.

`generate` logs, per game, the products, the products enriched by the source, the expansions and the share with an
image: compare them with the previous run before uploading. A game that fails (its source or its index) keeps its previous
files in `out/`, and the command exits with 1. `upload` refuses to start if a requested game has no catalog or no
index, or if its index was built from another catalog (run `index`). The TCGdex cards are cached in
`.cache/tcgdex/`, the card images and their hashes in `.cache/vision/`: keep that folder between runs, without it every
image is downloaded again.

## Environment variables

Only `upload` uses them:

- `CARDLEDGER_S3_BUCKET`: the bucket (required)
- `CARDLEDGER_S3_ENDPOINT`: endpoint of the S3-compatible service (optional, empty means AWS)
- `CARDLEDGER_S3_ACCESS_KEY_ID`, `CARDLEDGER_S3_SECRET_ACCESS_KEY`: the credentials (required)
- `CARDLEDGER_S3_REGION`: the bucket's region (required)

The credentials need only `s3:PutObject` on the bucket. `upload` reads them from the environment or from the `.env` file
in the working directory (the environment wins).

## CI

`tests.yml` runs the tests on every push to `main`.
