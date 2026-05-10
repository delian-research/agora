  ---
  Repository Audit — agora

  1. Layout
````
  agora/                                        Project root
  ├── pyproject.toml                            Package config (uv-managed)
  ├── uv.lock                                   Pinned deps
  ├── .env                                      MASSIVE_API_KEY (NOT gitignored — risk)
  ├── .gitignore                                Only excludes __pycache__, .venv
  ├── README.md                                 Design notes (long; some stale)
  ├── AGENTS.md                                 Working architecture doc
  │
  ├── agora/                                    Source package
  │   ├── __init__.py                           Public exports
  │   ├── config.py                             MassiveConfig dataclass + from_env()
  │   ├── client.py                             MassiveClient orchestrator
  │   ├── errors.py                             Exception hierarchy
  │   ├── py.typed                              PEP 561 marker (consumers read type hints)
  │   ├── models.py                             EMPTY (placeholder; safe to delete)
  │   │
  │   ├── equities/                             Recommended user-facing API
  │   │   ├── market.py                         get_daily_prices/returns/volume/snapshot
  │   │   ├── reference.py                      Stubs (NotImplementedError)
  │   │   ├── cax/                              Corporate actions (dividends, splits)
  │   │   └── company/                          Stubs (NotImplementedError; Benzinga-gated)
  │   │
  │   ├── loaders/                              Data access layer
  │   │   ├── rest.py                           MassiveDataApi (retry wrapper)
  │   │   ├── parquet.py                        FlatFileLoader (Parquet reader)
  │   │   ├── s3.py                             Deprecation shim → parquet.py
  │   │   └── socket.py                         WebSocketStreamer
  │   │
  │   ├── adapters/
  │   │   └── market.py                         Deprecation shim → equities.market
  │   │
  │   ├── normalize/                            Payload → DataFrame transforms
  │   │   ├── __init__.py                       Re-exports
  │   │   ├── base.py                           snake_case + epoch inference
  │   │   ├── ohlc.py                           OHLC payload normalize
  │   │   ├── snapshot.py                       Snapshot payload
  │   │   └── corporate_actions.py              Splits/divs payload
  │   │
  │   └── download/                             Bulk ingestion pipeline
  │       ├── __init__.py                       Public exports (incomplete)
  │       ├── __main__.py                       python -m agora.download
  │       ├── cli.py                            argparse commands
  │       ├── config.py                         S3 creds, DATA_DIR, rate-limit
  │       ├── checkpoint.py                     JSON resume tracker
  │       ├── stocks.py                         S3 flat-files → yearly Parquet
  │       ├── forex.py                          REST → daily_usd.parquet
  │       └── reference.py                      Tickers, exchanges, splits, divs, events
  │
  ├── data/                                     Downloaded Parquet (327 MB; NOT gitignored)
  │   ├── stocks/daily/{2021..2026}.parquet
  │   ├── forex/daily_usd.parquet
  │   └── reference/{tickers,exchanges,splits,dividends,ticker_events}.parquet
  │
  ├── dovs/                                     Docs ("docs" typo)
  │   ├── c.download.md                         Comprehensive download/API docs
  │   └── RestApi.md                            EMPTY
  │
  └── notebooks/                                Empty

  ★ Insight ─────────────────────────────────────
  The intended architecture per README.md and AGENTS.md is: loaders ingest, normalize flattens, adapters present analytics-ready APIs, client orchestrates. The
  download pipeline is self-contained (skips the loader/normalize stack entirely). When the older code was kept around, a few find-and-replace renames (e.g.
  polygon → massive, massive_data → agora) were applied incompletely — that's the source of most of the bugs below.
  ─────────────────────────────────────────────────

  ---
  2. Module-by-module summary

  Status as of 2026-04-29 (post-audit fixes — see dovs/1.Updates.md):

  ┌──────────────────────────────────────┬────────────────────────────────┬────────────────────────┬───────────┐
  │                Module                │            Purpose             │         Status         │   Lines   │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/__init__.py                    │ Package init + __version__     │ Working                │ 11        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/config.py                      │ MassiveConfig from env         │ Working                │ 92        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/errors.py                      │ Exception hierarchy            │ Working                │ 20        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/models.py                      │ Pydantic models (planned)      │ Empty (placeholder)    │ 0         │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/client.py                      │ MassiveClient orchestrator     │ Working (rewritten)    │ 100       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/loaders/rest.py                │ MassiveDataApi retry wrapper   │ Working                │ 274       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/loaders/parquet.py             │ FlatFileLoader Parquet reader  │ Working (renamed)      │ 445       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/loaders/s3.py                  │ Deprecation shim → parquet.py  │ Working (warns)        │ 33        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/loaders/socket.py              │ WebSocketStreamer              │ Working                │ 425       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/adapters/market.py             │ Deprecation shim → equities    │ Working (warns)        │ 130       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/equities/market.py             │ Domain prices/returns/snapshot │ Working                │ ~510      │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/equities/cax/*                 │ Dividends + splits wrappers    │ Working                │ ~110      │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/equities/reference.py          │ Stubs (exchange/currency/...)  │ NotImplementedError    │ ~80       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/equities/company/*             │ Stubs (industry/earnings/news) │ NotImplementedError    │ ~80       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/__init__.py          │ Re-exports                     │ Working                │ 28        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/base.py              │ snake_case + epoch handling    │ Working                │ 101       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/ohlc.py              │ OHLC payload                   │ Working                │ 163       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/snapshot.py          │ Snapshot payload               │ Working                │ 69        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/corporate_actions.py │ Splits/divs payload            │ Working                │ 38        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/download/*                     │ All download submodules        │ Working                │ 686 total │
  └──────────────────────────────────────┴────────────────────────────────┴────────────────────────┴───────────┘
```