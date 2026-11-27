# Manual corrections

`corrections/<slug>.toml` (slugs: mtg, pokemon, lorcana, onepiece, yugioh, riftbound) fixes cards and sets of the
game's own dataset by hand. It is applied on every run of `generate`, after the source is read. Entries that point to
a card or set the source no longer has are skipped with a warning; invalid TOML, a wrong type or an unknown key or
section fails the run. Sealed products are in [sealed/](../sealed/README.md).

Cards are found by catalog id (`mtg:lea:161`, with the variant appended when there is one):

```toml
[[card]]
id = "mtg:lea:161"         # required; every other field is optional
name = "..."
number = "161a"
rarity = "rare"
image_source = "https://..."   # URL, downloaded and hosted like the source's images
drop = true                # removes the card; other fields are then ignored
```

The id never changes when a field is fixed, so `number` does not move it.

Sets are found by code:

```toml
[[set]]
code = "old"               # required
source_code = "new"        # the source's code: renames it to `code` and rebuilds the ids of its cards
name = "..."
released_at = 2022-12-02
```

Without `source_code` the entry only changes the name or date of the set `code`. Set pins run before card fixes, so
`[[card]]` ids use the new code.
