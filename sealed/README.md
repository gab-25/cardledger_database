# Sealed products

No source lists sealed products, so they are written by hand in `sealed/<slug>.toml` (slugs: mtg, pokemon, lorcana,
onepiece, yugioh, riftbound). Every file starts empty, MTG included. Each product becomes one catalog row with the id
`<slug>:sealed:<key>`. Invalid TOML, a missing field, a wrong type, an unknown key or a category outside the list fails
the run; a missing file means no sealed products.

```toml
[[product]]
key = "lea-booster-box"            # unique per game, becomes part of the id
name = "Limited Edition Alpha Booster Box"
set_code = "lea"
category = "booster_box"           # booster, booster_box, bundle, case, deck or other
image_source = "https://..."       # optional: URL, downloaded and hosted like the source's images
```
