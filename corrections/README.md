# Manual corrections

`corrections/<slug>.toml` (slugs: mtg, pokemon, lorcana, onepiece, yugioh, riftbound) fixes wrong or missing images,
rarities and set codes of products by hand (expansion names are in [expansions/](../expansions/README.md)). It is applied on every run of `generate`, after the automatic match. Entries that point to a
product no longer in the Cardmarket list are skipped with a warning; invalid TOML, a wrong type or an
unknown key fails the run.

```toml
[[product]]
id_product = 123456
set_code = "op05"          # every field except id_product is optional
collector_number = "OP05-078"
rarity = "SR"
image_url = "https://..."
image_large_url = "https://..."
unmatched = true           # drops the automatic match (no image/rarity); other fields are then ignored
```

A product that was not matched gets a new card only if `set_code` is given (missing fields are left empty).
