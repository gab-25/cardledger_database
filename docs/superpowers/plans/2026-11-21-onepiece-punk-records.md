# One Piece from punk-records Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Piece takes set code, number, rarity and image from punk-records instead of TCGCSV.

**Architecture:** A new source module `sources/punkrecords.py` downloads two JSON files and, given the Cardmarket
products, returns an `Enrichment` (cards keyed by Cardmarket id, sets, expansion → set code). `cli._enrichment`
dispatches `source == "punkrecords"` to it. `assemble` and corrections are unchanged.

**Tech Stack:** Python 3.14, httpx, pytest with `httpx.MockTransport`, uv.

**Spec:** `docs/superpowers/specs/2026-11-21-onepiece-punk-records-design.md`

## Global Constraints

- URLs: `https://raw.githubusercontent.com/buhbbl/punk-records/main/english/packs.json` and
  `https://raw.githubusercontent.com/buhbbl/punk-records/main/english/index/cards_by_id.json`.
- Any `httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError` → `SourceError("punk-records <file>: ...")`.
- Vote: top pack needs ≥ 80% of the expansion's coded singles and strictly more votes than the second.
- Rarity map: Common C, Uncommon UC, Rare R, SuperRare SR, SecretRare SEC, Leader L, Special SP, TreasureRare TR,
  Promo P; unknown values pass through.
- Set code: label lower case, dash removed only in `XX-NN` form (`OP-05` → `op05`, `PRB-01` → `prb01`), compound labels
  lower case as is (`OP14-EB04` → `op14-eb04`); no label → `pk<pack id>`.
- `image_url` and `image_large_url` both = the variant's `img_url` (truncated to 500 like the other sources).
- Only Cardmarket singles (`id_category in singles`) are enriched; non-singles get nothing.
- Output format v1 unchanged; Yu-Gi-Oh! and Riftbound untouched (they stay on TCGCSV for now).

## Review Focus

- A code with no punk-records entry (`P-001` promos) → no card, no crash.
- An expansion whose singles have no code at all (DON!! only) → not mapped, no division by zero in the vote.
- Two Cardmarket products with the same code in one expansion → neither gets an image (rarity still allowed).
- A variant whose `rarity` is missing/empty → treated as its own rarity value "" (so a code with "" and "Rare" has no sure rarity).
- `cards_by_id.json` or `packs.json` returns `[]`/`{}` (as `japanese` does today) → empty enrichment, not an exception.

---

### Task 1: punk-records source

**Files:**
- Create: `cardledger_database/sources/punkrecords.py`
- Test: `tests/test_punkrecords.py`

**Interfaces:**
- Consumes: `model.CmProduct`, `model.SourceCard`, `model.SourceSet`, `model.Enrichment`, `model.SourceError`,
  `model.code_of(name) -> str | None`.
- Produces:
  - `PACKS_URL: str`, `CARDS_URL: str`
  - `set_code(pack_id: str, label: str | None) -> str`
  - `build(packs: dict, cards: dict, products: list[CmProduct], singles: set[int]) -> Enrichment` (pure, no I/O)
  - `fetch(client: httpx.Client, products: list[CmProduct], singles: set[int]) -> Enrichment` (downloads, then `build`)

- [ ] **Step 1: Write the failing tests** in `tests/test_punkrecords.py`, with a fixture of two packs
  (`"569105"` label `OP-05` title "Awakening of the New Era"; `"569202"` label `EB-02` title "Anime 25th Collection") and
  cards `OP05-001` (Common, 569105), `OP05-098` (Leader, 569105), `OP05-098_p1` (Leader, 569105),
  `OP05-098_p2` (Leader, 569202), `OP05-060` (Rare, 569105), `OP05-060_p1` (SuperRare, 569202).
  Cardmarket singles (category 1) in expansion 10: `"A (OP05-001)"` id 1, `"Enel (OP05-098)"` ids 2 and 3,
  `"B (OP05-060)"` id 4, `"Promo (P-001)"` id 5, `"DON!!"` id 6; in expansion 20: `"Enel (OP05-098)"` id 7 and
  `"B (OP05-060)"` id 9; a non-single (category 2) `"Box (OP05-001)"` id 8 in expansion 10.
  - `test_set_code`: `set_code("569105","OP-05") == "op05"`, `set_code("x","PRB-01") == "prb01"`,
    `set_code("x","OP14-EB04") == "op14-eb04"`, `set_code("569901", None) == "pk569901"`, `set_code("569901","") == "pk569901"`.
  - `test_expansion_vote`: `e.expansions == {10: "op05"}` — expansion 10: 569105 has 4/4 votes, 569202 3/4 → mapped;
    expansion 20: both packs 2/2 → tie → not mapped.
  - `test_unique_variant_gets_image`: `e.cards[1] == SourceCard("op05", "OP05-001", "C", url, url)` with the fixture's
    `img_url`; `e.cards[4]` has rarity `"R"` and an image (only `OP05-060` is in pack 569105).
  - `test_same_code_twice_in_expansion_has_no_image`: ids 2 and 3 → `SourceCard("op05", "OP05-098", "L", "", "")`.
  - `test_unmapped_expansion`: id 7 (all variants Leader) → `SourceCard("op05", "OP05-098", "L", "", "")` (set code
    from the base code's pack); id 9 (Rare vs SuperRare) not in `e.cards`.
  - `test_unknown_code_dons_and_non_singles_skipped`: 5, 6, 8 not in `e.cards`.
  - `test_sets`: `e.sets["op05"] == SourceSet("op05", "Awakening of the New Era", None)`.
  - `test_rarity_passthrough`: a card with rarity `"Mystery"` → `rarity == "Mystery"`.
  - `test_empty_files`: `build({}, {}, products, {1}).cards == {}` and `build([], [], products, {1})` does not raise.
  - `test_http_error` / `test_bad_json`: a MockTransport returning 500 or `b"not json"` → `pytest.raises(SourceError, match="punk-records")`.
  - `test_fetch_urls`: the MockTransport asserts only `PACKS_URL` and `CARDS_URL` are requested.

- [ ] **Step 2: Run** `uv run pytest tests/test_punkrecords.py -q` — Expected: FAIL (module not found).

- [ ] **Step 3: Implement** `cardledger_database/sources/punkrecords.py` per the Interfaces and the spec's
  Design steps 1–4. `packs.json` is a dict `{pack_id: {"id", "raw_title", "title_parts": {"label", "title", "prefix"}}}`;
  `cards_by_id.json` is a dict `{key: {"card_id", "pack_id", "rarity", "img_url", ...}}` where `key` is `<code>` or
  `<code>_pN`. Treat a non-dict file as empty. The base code of a key is the part before `_`. Votes count each single
  once per distinct pack among its code's variants; the share is votes / coded singles of that expansion found in
  punk-records. Set name: `title_parts.title`, else `raw_title`, else the code.

- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass.

- [ ] **Step 5: Commit** `git add cardledger_database/sources/punkrecords.py tests/test_punkrecords.py && git commit -m "Add the punk-records source for One Piece"`

### Task 2: Switch One Piece to punk-records

**Files:**
- Modify: `cardledger_database/model.py:16-30` (comment on `source`; `Game("onepiece", 18, "One Piece Card Game", True, "punkrecords")`)
- Modify: `cardledger_database/cli.py:18-34` (import `punkrecords`; `if game.source == "punkrecords": return punkrecords.fetch(client, products, singles)`)
- Modify: `scripts/run_bench.py:29` (`TCGCSV_GAMES` without `onepiece`; docstring "for yugioh/riftbound")
- Modify: `README.md` (sources table: One Piece → [punk-records](https://github.com/buhbbl/punk-records), "Pipeline AGPL-3.0; data © Bandai/Shueisha, from the official card list"; TCGCSV row → Yu-Gi-Oh!, Riftbound)
- Test: `tests/test_cli.py` (fake `punkrecords.fetch` like `lorcanajson`)

**Interfaces:**
- Consumes: `punkrecords.fetch(client, products, singles) -> Enrichment` from Task 1.

- [ ] **Step 1: Update `tests/test_cli.py`**: import `punkrecords`; in `Fakes`,
  `monkeypatch.setattr(punkrecords, "fetch", lambda client, products, singles: source("onepiece"))`. Add
  `test_onepiece_does_not_use_tcgcsv`: make `tcgcsv.fetch` raise, `cli.generate(["onepiece"], ...) == []`.
- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py -q` — Expected: the new test FAILS.
- [ ] **Step 3: Make the model, cli, run_bench and README changes listed under Files.**
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass.
- [ ] **Step 5: Real run**: `uv run cardledger-db generate --game onepiece --out <scratch>` — Expected: exit 0, and
  compared with TCGCSV (enriched 2,879, expansions 153, with_image 20.9%): enriched ≫ 2,879 (≈ 11,000), with_image
  ≈ 45%, far fewer `cmx` expansions. Then print 20 random singles with an image (id, name, expansion code, rarity,
  image URL) for the user to spot-check on Cardmarket.
- [ ] **Step 6: Commit** `git add -A cardledger_database scripts tests README.md && git commit -m "One Piece from punk-records instead of TCGCSV"`
