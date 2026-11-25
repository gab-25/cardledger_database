# Expansion names

`expansions/<slug>.toml` names Cardmarket expansions by hand. Cardmarket's product lists carry only the expansion id,
so an expansion the game's source has no set for gets the stand-in code `cmx<idExpansion>` and the name
"Cardmarket expansion #<idExpansion>"; an entry here gives it a real name. It is applied on every run of `generate`,
together with `corrections/`. An entry for an expansion no longer in the Cardmarket list is skipped with a warning;
invalid TOML, a wrong type, an unknown key or a `[[product]]` section fails the run.

```toml
[[expansion]]
id_expansion = 1645
code = "cmx1645"           # keep the stand-in code; any code works (it maps the whole expansion to it)
name = "Pokémon Products"
released_at = 2016-01-01   # optional
```

An entry also overrides the name and date of a set the source already has, if its `code` is that set's code. The
current entries were pre-filled from the names of each expansion's sealed products (boosters, boxes, tins) and
reviewed by hand; expansions with no sealed product are listed as comments at the end of each file, still unnamed.
