# Expansion names

`expansions/<slug>.toml` names Cardmarket expansions by hand. Cardmarket's product lists carry only the expansion id,
so an expansion the game's source has no set for gets the stand-in code `cmx<idExpansion>` and the name
"Cardmarket expansion #<idExpansion>"; an entry here gives it a real name. It is applied on every run of `generate`,
together with `corrections/`. An entry for an expansion no longer in the Cardmarket list is skipped with a warning;
invalid TOML, a wrong type, an unknown key or a `[[product]]` section fails the run.

```toml
[[expansion]]
id_expansion = 1645
code = "cmx1645"           # the stand-in code: names it, products still follow the source once it has the set
name = "Pokémon Products"
released_at = 2016-01-01   # optional
```

An entry with the stand-in code only names it: when the source later adds the set, the expansion's products move to
it as usual and the entry is simply unused. Any other `code` maps the whole expansion to that code for good, and
overrides the name and date of a source set with that code. The
current entries were pre-filled from the names of each expansion's sealed products (boosters, boxes, tins) and
reviewed by hand; expansions with no sealed product are listed as comments at the end of each file, still unnamed.
