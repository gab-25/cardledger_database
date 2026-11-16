# cardledger_database

Nightly generator of the CardLedger card catalog for six trading card games (Magic, Pokémon, Lorcana, One Piece,
Yu-Gi-Oh!, Riftbound), built from public sources and published as files to S3.

```bash
uv sync
uv run pytest -q
```

S3 credentials come only from the environment: `CARDLEDGER_S3_BUCKET`, `CARDLEDGER_S3_ENDPOINT` (optional),
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`. Generated files (`out/`) and caches (`.cache/`)
are never committed.
