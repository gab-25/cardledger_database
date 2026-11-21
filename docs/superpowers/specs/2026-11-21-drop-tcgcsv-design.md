# Yu-Gi-Oh! from yaml-yugi, Riftbound from Riftcodex, no more TCGCSV

## Why

The user wants the catalog off TCGCSV (TCGplayer data, no licence) entirely. One Piece already moved to punk-records.
Yu-Gi-Oh! moves to [yaml-yugi](https://github.com/DawnbrandBots/yaml-yugi) (AGPL-3.0, card data © Konami) and Riftbound to
[Riftcodex](https://riftcodex.com) (fan API, no licence stated, images from Riot's CDN). Neither carries Cardmarket
product ids and Cardmarket names carry no card code for these games, so both keep going through the name matcher.

## Measured (2026-11-21, prototype in the scratchpad)

| | TCGCSV today | matcher + new source | + shared-printing fill-in |
| --- | --- | --- | --- |
| Yu-Gi-Oh! singles enriched | 26,404 (30%) | 25,721 (29%) | 35,527 (40%) (loose fill-in) |
| Yu-Gi-Oh! singles with image | ~25,600 | 22,267 | 29,671 |
| Riftbound singles enriched | 939 (50%) | 765 (41%) | 1,075 (57%) |
| Riftbound singles with image | 939 | 765 | 873 |

With the final fill-in rule (below: no more Cardmarket singles than source printings for the key) Yu-Gi-Oh! is at 34,414
(39.2%) and Riftbound at 1,066 (56.5%). The bench prototype on MTGJSON: pairs 51,346 judged, 6 wrong (0.01%); fill-ins
with the loose rule 82 wrong of 256 (32%, mostly Commander oversized and promo sub-sets sharing a Cardmarket expansion),
with the final rule 0 wrong of 1. A rule on differing `id_metacard` was also measured and changed nothing, so it is not
added.

Real runs after implementation: Yu-Gi-Oh! 34,414 enriched, with_image 32.2%. Riftbound 850 enriched, with_image 42.3%,
below TCGCSV's 939: Riftcodex has 147 duplicate records (deduplicated, latest update wins) and an ambiguous
`collector_number` (4 = rune R04, card 004, showcase SP4), so numbers come from `riftbound_id`; base and variant
numbers then differ (229 vs 229*) and those ~200 products are no longer filled in with the base number.

## Goals

- Yu-Gi-Oh! and Riftbound enriched from yaml-yugi and Riftcodex through the existing matcher, plus a fill-in step.
- `tcgcsv.py` deleted; nothing in the repository downloads from TCGCSV, the bench included.
- The bench keeps guarding the matcher (≤ 1% wrong cards and expansions on Magic), now judging fill-ins too.

## Non-goals

- No change to the matcher's thresholds or pairing rules (`MIN_COMMON`, `MIN_SHARE`, `MIN_MARGIN`,
  `MAX_PRINTING_RATIO`, `best_group`, `best_expansion`, `printing_ratio`, `pair_cards`).
- Riftcodex's per-set `cardmarket_id` is not used: it covers 5 of the 17 Cardmarket expansions (Unleashed and Vendetta
  have none).
- No image hosting: the catalog keeps storing URLs only.
- Output format v1 unchanged.

## Approaches

1. **Existing matcher + fill-in, both sources adapted to the matcher's input (chosen).** The matcher is benched (≤ 1%
   errors on Magic); the fill-in uses the same "only when every candidate agrees" rule as One Piece.
2. A looser matcher for Yu-Gi-Oh! (prototype: 68% of singles with a number): rejected, its errors are unmeasured and a
   wrong expansion pairing puts wrong data on hundreds of cards.
3. Keep TCGCSV only inside the bench: rejected, the user wants no TCGCSV at all; benching on MTGJSON also measures the
   fill-in.

## Design

### Matcher input types

`TcgGroup` and `TcgProduct` move from `sources/tcgcsv.py` to `matcher.py` and become source-neutral:

- `Group(group_id: int, name: str, abbreviation: str, published_on: date | None)`
- `Printing(printing_id: int, group_id: int, name: str, number: str, rarity: str, image_url: str, image_large_url: str)`

The TCGplayer-specific `large_image()` (`_200w` → `_in_1000x1000`) goes away: each source fills `image_large_url`.

`set_codes()` stays (lower-case abbreviation, unique) but its fallback for an empty or shared abbreviation must be
stable across runs, because group ids are now synthetic: `<abbreviation or "set">-<slug of the group name>`, lower
case, `[a-z0-9-]` only, truncated to 40 characters.

### Fill-in

New step in `match`, after `pair_cards` for each paired expansion: every single of the expansion that has a key but no
card gets one when the group's printings with that key are ≥ 1, all share the same `number`, and the expansion has no
more singles with that key than the group has printings with it (Cardmarket twins that are rarity variants pass;
a Cardmarket regular + oversized pair against one printing does not):

- set code: the group's code; number: `code_of(name) or number` (as for pairs);
- rarity: the shared rarity when all candidates agree, else `""`;
- image URLs: the shared URLs when all candidates have the same non-empty `image_url`, else `""`.

Fill-ins go in `enrichment.cards`, not in `card_pairs` (they have no single source printing).

`test_variants_are_not_paired_by_id_order` changes accordingly: the two Luffy OP01-001 variants now get the shared
number (and rarity if equal) but no image, since their printings' images differ; they are still not in `card_pairs`.

### yaml-yugi (`sources/yamlyugi.py`)

`fetch(client) -> tuple[list[Group], list[Printing]]` from `https://dawnbrandbots.github.io/yaml-yugi/cards.json`
(about 110 MB, ~380 MB of RAM once parsed; downloaded on every run, like MTGJSON).

- Only English: `name.en` and `sets.en`. Cards without an English name are skipped.
- One group per English `set_name`; `group_id` by sorted set name; abbreviation: the most common prefix of its
  `set_number`s (the part before `-`, e.g. `SAST`); `published_on` None.
- One printing per (card, English set entry, rarity); `number` = `set_number`; an entry without rarities gives one
  printing with rarity `""`.
- Image: only when the card has exactly one entry in `images`: the Yugipedia URL of its file,
  `https://img.yugipedia.com/<md5[0]>/<md5[0:2]>/<file>` (md5 of the file name as given); same URL for both sizes.
  Checked working on 2026-11-21. Yugipedia states no hotlinking rule; its text is CC BY-SA, the images are © Konami.

### Riftcodex (`sources/riftcodex.py`)

`fetch(client) -> tuple[list[Group], list[Printing]]` from `https://api.riftcodex.com`:

- `/sets?size=100`: one group per set; abbreviation `set_id` (e.g. `OGN`), name `name`, `published_on` the date part
  of `published_on`.
- `/cards?size=100&page=N` for every page (`size` above 100 is rejected; `pages` comes with page 1): one printing per
  card. Name: `name` with `" - "` replaced by `", "` (Cardmarket writes "Miss Fortune, Buccaneer"; the matcher drops
  everything after `" - "`). `number` = `collector_number` as a string, rarity `classification.rarity`, both image URLs
  `media.image_url`, group by `set.set_id`.
- The API is slow (single pages took 20 s–106 s on 2026-11-21): pages are fetched with up to 4 parallel requests through
  `http.get` (retries), with a per-request timeout of 300 s for this source.

### Wiring

- `Game`: Yu-Gi-Oh! source `"yamlyugi"`, Riftbound `"riftcodex"`; the `tcgcsv_category` field is removed.
- `cli._enrichment`: those two call their `fetch`, then `match(products, groups, printings, singles).enrichment`.
- `sources/tcgcsv.py` and `tests/test_tcgcsv.py` are deleted; tests using `TcgGroup`/`TcgProduct` move to the new types.
- README: sources table rows for Yu-Gi-Oh! and Riftbound; no TCGCSV mention left.

### Bench (`scripts/run_bench.py`)

Matcher input for Magic now comes from MTGJSON instead of TCGCSV, and the truth is MTGJSON's Cardmarket ids:

- MTGJSON gives the matcher groups (one per set, abbreviation = set code, name, release date) and printings (front faces:
  name, number, rarity, Scryfall image URLs), ignoring the Cardmarket ids.
- Truth: MTGJSON's enrichment `{Cardmarket id: SourceCard}`.
- A produced card (pair or fill-in) is wrong when its Cardmarket id is in the truth and its (set code, normalized
  number) differs from the truth's. An expansion pairing is wrong when its set code is not the most frequent true set
  code of its singles (ties: any).
- Pairs and fill-ins are reported separately and both count toward the card error rate.
- Still exits 1 above 1% for cards or expansions; still prints, for yugioh and riftbound, the share of Cardmarket
  singles with a card (now from the new sources).

MTGJSON names Cardmarket the same way more often than TCGplayer does, so the bench is easier than before: it remains a
regression guard, not an estimate of the error on the other games.

## Verification

- Unit tests for the new types, the fill-in (agree / disagree on number, rarity, image), the stable fallback set code,
  both sources' parsing (fixtures, `httpx.MockTransport`), Riftcodex pagination, and the bench's judging.
- `uv run python scripts/run_bench.py` passes (≤ 1%).
- Real `generate --game yugioh` and `--game riftbound` into a scratch folder: compare with the table above.
- The user spot-checks a few products per game on Cardmarket.

## Expansion codes

The CardLedger app identifies products by `id_product` (user, 2026-11-21); expansion codes only group them, so they may
change between runs (they all change once with this switch). No migration needed.

## Calls made by Claude (check these)

- Yugipedia images only for cards with a single artwork.
- Fill-in leaves rarity empty when ambiguous rather than guessing.
- Stable fallback set code from the group name.
- Riftcodex per-request timeout 300 s and 4 parallel pages.
