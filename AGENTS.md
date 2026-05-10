# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Repository purpose
`agora` is a Python package for market data ingestion and local storage, centered on Massive.com data sources:
- Bulk historical US equities via S3 flat files
- Forex and reference datasets via Massive REST API
- Local Parquet outputs under `data/` for offline analysis

## Environment and setup
- Python requirement: `>=3.13` (from `pyproject.toml`)
- Dependencies are defined in `pyproject.toml` and pinned in `uv.lock`
- Environment variable required for API access: `MASSIVE_API_KEY` (loaded from `.env` in multiple modules)

Common setup commands:
- Install dependencies with uv lockfile:
  - `uv sync`
- Alternative editable install:
  - `pip install -e .`

## Core development commands
### Run the downloader CLI
The project defines a console script in `pyproject.toml`:
- `agora-download --help`

Equivalent module form:
- `python -m agora.download --help`

Common workflows:
- Download everything:
  - `python -m agora.download all`
- Download only stocks:
  - `python -m agora.download stocks --start-year 2021 --end-year 2026`
- Download only forex:
  - `python -m agora.download forex`
- Download reference tables:
  - `python -m agora.download reference`
- Download ticker event history/security-master table:
  - `python -m agora.download events`
- Sync the live security master + append timestamped change log:
  - `python -m agora.download security-master`
  - One-shot full-history backfill: `python -m agora.download security-master --full-event-backfill`
  - Universe-allow-partial mode: `python -m agora.download security-master --allow-partial`
- Force full re-download (ignore checkpoints):
  - `python -m agora.download all --no-resume`

### Build, lint, and test status
- Run the smoke suite: `pytest` (~200 tests, runs in ~7s)
- Run a single test: `pytest tests/test_imports.py::test_module_imports`
- CI: `.github/workflows/test.yml` runs the same `pytest` invocation on
  every push and PR to `dev` or `main`.

### Branching
- Default branch is `dev` — feature work lands here first via PR.
- `main` is the stable / released branch; updated by promoting `dev`.
- Both branches are protected: PR required, `test` CI must pass, no
  force-push, no deletion.

## High-level architecture
### 1) Download pipeline (`agora/download`)
This is the main ingestion path and current operational center of the repo.
- `cli.py`: argparse command surface (`stocks`, `forex`, `reference`, `events`, `security-master`, `all`)
- `stocks.py`: S3 flat-file ingestion (`us_stocks_sip/day_aggs_v1`) into yearly Parquet files
- `forex.py`: REST `list_aggs` ingestion for `*USD` FX pairs into one Parquet file
- `reference.py`: REST ingestion for tickers/exchanges/splits/dividends plus ticker-event security master
- `security_master.py`: incremental sync that produces a current-state master + append-only timestamped change log; reads `data/indices_included.csv` for the index allowlist
- `checkpoint.py`: resumable download tracking via JSON checkpoint files
- `config.py`: download-specific constants (S3 endpoint/bucket, output `data/` path, REST rate-limit setting)

Output contract (read by other modules):
- `data/stocks/daily/{year}.parquet`
- `data/forex/daily_usd.parquet`
- `data/reference/{tickers,exchanges,splits,dividends,ticker_events}.parquet`
- `data/reference/security_master.parquet` (current state) + `data/reference/security_master_changes.parquet` (timestamped change log) + `data/reference/snapshots/tickers_<YYYY-MM-DD>.parquet`

### 2) Data access layer (`agora/loaders`)
Two retrieval patterns are represented:
- `loaders/parquet.py` (`FlatFileLoader`): read-only local Parquet access with helpers for:
  - stock/forex retrieval
  - reference tables
  - ticker event history, ticker resolution by date, and continuous series across symbol changes
- `loaders/rest.py` (`MassiveDataApi`): API wrapper with retry/backoff and error normalization around Massive `RESTClient`
- `loaders/socket.py`: currently empty placeholder

### 3) Normalization layer (`agora/normalize`)
Transforms API payloads into analysis-ready DataFrames:
- `base.py`: key normalization (`snake_case`), flattening nested payloads, epoch inference and UTC conversion
- `ohlc.py`: grouped daily/open-close/aggregate/previous-day payload normalization
- `snapshot.py`: snapshot payload flattening
- `corporate_actions.py`: dividends and splits normalization

### 4) Client and adapter layer
- `client.py`: intended high-level client/orchestration entry point with singleton access (`get_client()`)
- `adapters/market.py`: user-facing analytics helpers (`get_prices`, `get_returns`) built on top of client access

## Practical implementation notes for future agents
- If changing data schema in downloader outputs, also update `FlatFileLoader` assumptions in `agora/loaders/parquet.py`.
- Download resumability is checkpoint-driven; changing key logic in `checkpoint.py` or per-task checkpoint keys in downloader modules affects restart behavior.
- Rate-limit behavior is centralized in download modules (notably forex/reference); keep limits consistent with subscription constraints when modifying request loops.
- `dovs/c.download.md` contains detailed operational behavior and historical constraints discovered during implementation; consult it before changing ingestion logic.
