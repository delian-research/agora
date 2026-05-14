  ---
  Repository Audit — agora

  1. Layout
````
  agora/                                        Project root
  ├── pyproject.toml                            Package config (uv-managed)
  ├── uv.lock                                   Pinned deps
  ├── .env                                      MASSIVE_API_KEY (gitignored; see .env.example)
  ├── .gitignore                                Excludes caches, envs, generated data, IDE files
  ├── README.md                                 User-facing overview and module map
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
  │   │   ├── reference.py                      Tickers, details, types, exchanges, related tickers
  │   │   ├── cax/                              API-first corporate actions (dividends, splits)
  │   │   └── company/                          SIC classification + Benzinga-gated stubs
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
  │       ├── __init__.py                       Public exports
  │       ├── __main__.py                       python -m agora.download
  │       ├── cli.py                            argparse commands
  │       ├── config.py                         S3 creds, DATA_DIR, rate-limit
  │       ├── checkpoint.py                     JSON resume tracker
  │       ├── stocks.py                         S3 flat-files → yearly Parquet
  │       ├── forex.py                          REST → daily_usd.parquet
  │       └── reference.py                      Tickers, exchanges, splits, divs, events
  │
  ├── data/                                     Generated Parquet (gitignored except indices_included.csv)
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

  Status as of 2026-05-14 (post-equities API/doc cleanup — see dovs/1.Updates.md):

  ┌──────────────────────────────────────┬────────────────────────────────┬────────────────────────┬───────────┐
  │                Module                │            Purpose             │         Status         │   Lines   │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/__init__.py                    │ Package init + __version__     │ Working                │ 100       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/config.py                      │ MassiveConfig from env         │ Working                │ 97        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/errors.py                      │ Exception hierarchy            │ Working                │ 20        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/models.py                      │ Pydantic models (planned)      │ Empty (placeholder)    │ 0         │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/client.py                      │ MassiveClient orchestrator     │ Working                │ 104       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/loaders/rest.py                │ MassiveDataApi retry wrapper   │ Working                │ 1057      │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/loaders/parquet.py             │ FlatFileLoader Parquet reader  │ Working                │ 632       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/loaders/s3.py                  │ Deprecation shim → parquet.py  │ Working (warns)        │ 32        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/loaders/socket.py              │ WebSocketStreamer              │ Working                │ 457       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/adapters/market.py             │ Deprecation shim → equities    │ Working (warns)        │ 130       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/equities/market.py             │ Domain prices/returns/snapshot │ Working                │ 1106      │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/equities/cax/*                 │ Dividends + splits wrappers    │ Working                │ 248 total │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/equities/reference.py          │ Reference/catalog helpers      │ Working                │ 399       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/equities/company/*             │ SIC classification + Benzinga  │ Partial: Benzinga stubs │ 218 total │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/__init__.py          │ Re-exports                     │ Working                │ 28        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/base.py              │ snake_case + epoch handling    │ Working                │ 101       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/ohlc.py              │ OHLC payload                   │ Working                │ 164       │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/snapshot.py          │ Snapshot payload               │ Working                │ 70        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/normalize/corporate_actions.py │ Splits/divs payload            │ Working                │ 38        │
  ├──────────────────────────────────────┼────────────────────────────────┼────────────────────────┼───────────┤
  │ agora/download/*                     │ All download submodules        │ Working                │ 2095 total │
  └──────────────────────────────────────┴────────────────────────────────┴────────────────────────┴───────────┘
```
