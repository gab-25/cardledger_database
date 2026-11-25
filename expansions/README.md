# Expansion names

`expansions/<slug>.toml` names Cardmarket expansions by hand. Cardmarket's product lists carry only the expansion id,
so an expansion the game's source has no set for gets the stand-in code `cmx<idExpansion>` and the name
"Cardmarket expansion #<idExpansion>"; an entry here gives it a real name. It is applied on every run of `generate`,
together with `corrections/`. An entry for an expansion no longer in the Cardmarket list is skipped with a warning;
an entry without `name` is skipped with a warning; invalid TOML, a wrong type, an unknown key or a `[[product]]` section
fails the run.

```toml
[[expansion]]
id_expansion = 1645
name = "Pokémon Products"
released_at = 2016-01-01   # optional
```

An entry only names the stand-in `cmx<id_expansion>`: it never moves products. When the source later adds the set,
the expansion's products move to it as usual and the entry is simply unused. Where the names come from:

- **Cardmarket's own names**, copied by hand (not scraped by the tool) from the expansion filter of each game's
  `https://www.cardmarket.com/en/<Game>/Products/Singles` page; the date is in each file's header. That filter lists
  only expansions with singles. Cardmarket's site is not an openly licensed source: this is a deliberate exception
  for a list of names. `mtg.toml` holds every Magic expansion this way (unused today: Magic keeps no unmatched
  products), so it is complete rather than limited to the stand-ins.
- **The names of an expansion's sealed products** (boosters, boxes, tins), reviewed by hand, for the stand-ins with no
  name from Cardmarket. Expansions with neither are listed as comments at the end of each file, still unnamed.
