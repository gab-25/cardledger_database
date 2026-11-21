# Drop TCGCSV Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Yu-Gi-Oh! from yaml-yugi and Riftbound from Riftcodex through the existing matcher plus a fill-in step; the
bench on MTGJSON; `tcgcsv.py` deleted.

**Architecture:** The matcher gets source-neutral input types (`Group`, `Printing`) and a fill-in step. Two new source
modules turn yaml-yugi and Riftcodex into those types. The bench feeds the matcher MTGJSON printings and judges every
produced card against MTGJSON's Cardmarket ids.

**Tech Stack:** Python 3.14, httpx (`http.get` retries), pytest + `httpx.MockTransport`, uv.

**Spec:** `docs/superpowers/specs/2026-11-21-drop-tcgcsv-design.md`

## Global Constraints

- Matcher thresholds and pairing rules unchanged: `MIN_COMMON`, `MIN_SHARE`, `MIN_MARGIN`, `MAX_PRINTING_RATIO`,
  `best_group`, `best_expansion`, `printing_ratio`, `pair_cards`.
- Fill-in condition: candidates ≥ 1, one shared `number`, and Cardmarket singles with the key in the expansion ≤
  candidates; rarity only if shared, images only if one shared non-empty `image_url`.
- Fallback set code: `<abbreviation or "set">-<slug of group name>`, lower case, `[a-z0-9-]`, ≤ 40 chars.
- yaml-yugi URL `https://dawnbrandbots.github.io/yaml-yugi/cards.json`; image
  `https://img.yugipedia.com/<md5[0]>/<md5[0:2]>/<file>` only when the card has exactly one `images` entry.
- Riftcodex base `https://api.riftcodex.com`; `size=100`; ≤ 4 parallel pages; per-request timeout 300 s; name
  `" - "` → `", "`.
- Bench: card error = (set code, normalized number) ≠ MTGJSON truth; exit 1 above 1% (cards or expansions).
- Every source error → `SourceError("<Source>: ...")`. Output format v1 unchanged.

## Review Focus

- A yaml-yugi card with `sets.en` missing or `null`, or `name.en` missing → skipped, no crash.
- A Riftcodex page that fails after retries, or returns fewer items than announced → `SourceError`, not a partial catalog.
- Two yaml-yugi sets whose names slug to the same fallback code → still two distinct codes (append the group id only then).
- Fill-in where candidates share the number but one has an empty image → no image.
- An expansion with fill-ins only (no unique pair) still gets its set code via `Enrichment.expansions`.

---

### Task 1: Source-neutral matcher input

**Files:** Modify `cardledger_database/matcher.py`, `cardledger_database/sources/tcgcsv.py`, `scripts/run_bench.py`;
Test `tests/test_matcher.py`, `tests/test_tcgcsv.py`, `tests/test_run_bench.py`.

**Interfaces:**
- Produces (in `matcher.py`): `Group(group_id: int, name: str, abbreviation: str, published_on: date | None)`;
  `Printing(printing_id: int, group_id: int, name: str, number: str, rarity: str, image_url: str, image_large_url: str)`;
  `set_codes(groups: list[Group]) -> dict[int, str]`; `match(products, groups: list[Group], printings: list[Printing], single_category_ids) -> MatchResult`
  (`card_pairs` values are `printing_id`). `large_image` and the `SMALL_IMAGE/LARGE_IMAGE` constants move to `tcgcsv.py`,
  which now builds `Printing`s with `image_large_url` filled (deleted in Task 6).

- [ ] **Step 1: Tests.** In `test_matcher.py` replace the `TcgGroup/TcgProduct` helpers with `Group/Printing`
  (`image_large_url = image_url.replace("_200w", "_in_1000x1000")` in the helper). Add
  `test_fallback_set_code_is_stable`: groups `(1, "Savage Strike", "")` and `(2, "Legend of Blue Eyes", "SDK")`,
  `(3, "Starter Deck: Kaiba", "SDK")` → `{1: "set-savage-strike", 2: "sdk-legend-of-blue-eyes", 3: "sdk-starter-deck-kaiba"}`;
  `test_fallback_set_code_collision`: two groups named "Promo" with abbreviation "" → `"set-promo"` for the lower id,
  `"set-promo-<id>"` for the other. Update the existing `set_codes` test that expects `tcg<id>`.
- [ ] **Step 2: Run** `uv run pytest -q` — Expected: FAIL (imports / new tests).
- [ ] **Step 3: Implement** the types and `set_codes` in `matcher.py`; `match` copies `image_large_url` from the
  printing; update `tcgcsv.py` (returns `list[Group], list[Printing]`), `run_bench.py` and the other tests' imports.
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** "Source-neutral matcher input (Group, Printing) and a stable fallback set code".

### Task 2: Fill-in

**Files:** Modify `cardledger_database/matcher.py`; Test `tests/test_matcher.py`.

**Interfaces:** Consumes Task 1 types. Produces the fill-in inside `match` (no new public function required).

- [ ] **Step 1: Tests** (one paired expansion built with `named_set()` plus extra items):
  - `test_fill_in_rarity_twins`: CM adds two "Echo Mage" singles (ids 20, 21); group adds two "Echo Mage" printings,
    number "9", rarities "Common" and "Rare", same image → both get `SourceCard(code, "9", "", img, large)`; neither in `card_pairs`.
  - `test_fill_in_same_rarity_kept`: same but both rarities "Rare" → rarity "Rare".
  - `test_no_fill_in_when_numbers_differ`: printings numbers "9" and "10" → 20, 21 not in cards.
  - `test_no_fill_in_when_cardmarket_has_more`: CM two "Echo Mage", group one printing → not in cards.
  - `test_fill_in_image_dropped_when_images_differ` and `..._when_one_image_empty` → image fields "".
  - Update `test_variants_are_not_paired_by_id_order`: 1 and 2 now get number "OP01-001", no image; `card_pairs` unchanged.
  - `test_unpaired_expansion_gets_no_fill_in`: the expansion fails `best_group` → no cards at all.
- [ ] **Step 2: Run** `uv run pytest tests/test_matcher.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the fill-in after `pair_cards` per the spec (keys from `cm_keys`, candidates by `tcg_key`
  in the expansion's mode).
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** "Matcher: fill in number, shared rarity and image for repeated names".

### Task 3: yaml-yugi source

**Files:** Create `cardledger_database/sources/yamlyugi.py`; Test `tests/test_yamlyugi.py`.

**Interfaces:** Produces `URL: str`, `image_url(file: str) -> str`, `parse(cards: list) -> tuple[list[Group], list[Printing]]`,
`fetch(client) -> tuple[list[Group], list[Printing]]`.

- [ ] **Step 1: Tests:** `image_url("TrickstarLycoris-OP09-EN-UtR-UE.png") == "https://img.yugipedia.com/7/73/TrickstarLycoris-OP09-EN-UtR-UE.png"`;
  a fixture of 3 cards (one with two English sets and two rarities in one, one with two `images`, one without `name.en`,
  one with `sets: null`) → groups sorted by name with ids 1.., abbreviation the most common prefix, printings one per
  (card, set, rarity), image only for the single-artwork card, skipped cards absent; `fetch` with a 500 / bad JSON →
  `SourceError` matching "yaml-yugi"; `fetch` requests only `URL`.
- [ ] **Step 2: Run** `uv run pytest tests/test_yamlyugi.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** per spec (download through `http.get`).
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** "Add the yaml-yugi source for Yu-Gi-Oh!".

### Task 4: Riftcodex source

**Files:** Create `cardledger_database/sources/riftcodex.py`; Test `tests/test_riftcodex.py`.

**Interfaces:** Produces `BASE: str`, `parse_sets(items: list) -> list[Group]` (group ids by sorted `set_id`),
`parse_cards(items: list, groups: list[Group]) -> list[Printing]`, `fetch(client) -> tuple[list[Group], list[Printing]]`.

- [ ] **Step 1: Tests:** sets fixture (OGN 2025-10-31, UNL no `cardmarket_id`) → groups with `published_on` dates;
  cards: "Vi - Piltover Enforcer (Signature)" → name "Vi, Piltover Enforcer (Signature)", number "229", rarity, both
  image URLs; a card whose set is not in `/sets` → its own group created from `set.set_id`/`set.label`;
  pagination: MockTransport serving `pages: 3` → all three pages requested (`page=1..3`, `size=100`) and items
  concatenated; a page answering 500 three times → `SourceError` matching "Riftcodex"; a 404 page → `SourceError`.
- [ ] **Step 2: Run** `uv run pytest tests/test_riftcodex.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement**: page 1 first (gives `pages`), the rest with `ThreadPoolExecutor(max_workers=4)` through
  `http.get`; per-request `timeout=300` (pass `timeout` to `client.get`, adding an optional `timeout` parameter to
  `http.get`).
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** "Add the Riftcodex source for Riftbound".

### Task 5: Bench on MTGJSON

**Files:** Modify `cardledger_database/sources/mtgjson.py`, `scripts/run_bench.py`, `cardledger_database/cli.py`
(`mtgjson.fetch(client)` now returns the `Enrichment` only); Test `tests/test_mtgjson.py`, `tests/test_run_bench.py`, `tests/test_cli.py`.

**Interfaces:**
- `mtgjson.fetch(client) -> Enrichment` (the TCGplayer truth is removed); `mtgjson.matcher_input(client) -> tuple[list[Group], list[Printing]]`
  (front faces of cards and tokens; group per set code with name and release date; printing name = the part before `" // "`).
- `run_bench.bench(result: MatchResult, truth: Enrichment) -> BenchResult` with `card_matches, card_errors,
  fill_matches, fill_errors, expansion_matches, expansion_errors`; `card_error_rate` counts pairs + fill-ins.

- [ ] **Step 1: Tests:** `test_mtgjson.py`: drop `test_truth_holds_tcgplayer_ids`, add `test_matcher_input` (groups and
  printings from the fixture CSVs). `test_run_bench.py`: rewrite on the new signature — a pair with the right
  (set, number) counts right, a wrong number counts wrong, a fill-in (id in `enrichment.cards` but not `card_pairs`) is
  judged in `fill_*`, products without truth ignored, expansion error when the group's code is not the most frequent
  true set code, `passed` false above 1%.
- [ ] **Step 2: Run** `uv run pytest -q` — Expected: FAIL.
- [ ] **Step 3: Implement**; `run_bench.main` downloads Cardmarket Magic + MTGJSON (cached with `--cache-dir` as now),
  runs the matcher on `matcher_input`, prints the result, then for yugioh and riftbound the share of singles with a card
  from the new sources.
- [ ] **Step 4: Run** `uv run pytest -q` then `uv run python scripts/run_bench.py --cache-dir <scratch>` — Expected:
  tests PASS; bench exit 0, pairs error ≈ 0.01%, fill-ins ≈ 0 wrong.
- [ ] **Step 5: Commit** "Bench the matcher on MTGJSON instead of TCGCSV".

### Task 6: Switch Yu-Gi-Oh! and Riftbound, delete TCGCSV

**Files:** Modify `model.py` (`Game` without `tcgcsv_category`; yugioh `"yamlyugi"`, riftbound `"riftcodex"`),
`cli.py`, `README.md`; Delete `sources/tcgcsv.py`, `tests/test_tcgcsv.py`; Test `tests/test_cli.py`.

- [ ] **Step 1: Tests:** in `test_cli.py` fake `yamlyugi.fetch` / `riftcodex.fetch` and `cli.match`; add
  `test_no_game_uses_tcgcsv`: `grep`-style check that `cardledger_database` and `scripts` contain no "tcgcsv".
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement**; README sources table: Yu-Gi-Oh! → yaml-yugi ("Pipeline AGPL-3.0; card data © Konami;
  images from Yugipedia"), Riftbound → Riftcodex ("Fan API, no licence stated; images from Riot's CDN"); remove TCGCSV;
  bench paragraph says MTGJSON.
- [ ] **Step 4: Run** `uv run pytest -q`, then `generate --game yugioh` and `--game riftbound` into a scratch folder —
  Expected: PASS; yugioh enriched ≈ 34,000, riftbound ≈ 1,060 (spec table). Print 5 random products with an image per
  game (id, name, set code, number, rarity, image URL) for the user's spot-check.
- [ ] **Step 5: Commit** "Yu-Gi-Oh! from yaml-yugi, Riftbound from Riftcodex; remove TCGCSV".
