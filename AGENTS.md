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
- Force full re-download (ignore checkpoints):
  - `python -m agora.download all --no-resume`

### Build, lint, and test status
There are currently no repository-defined build/lint/test commands (no `Makefile`, no tool sections for pytest/ruff/mypy, and no test directory in the current tree).

When adding tests, use standard pytest invocation:
- Run all tests: `pytest`
- Run a single test: `pytest path/to/test_file.py::test_name`

## High-level architecture
### 1) Download pipeline (`agora/download`)
This is the main ingestion path and current operational center of the repo.
- `cli.py`: argparse command surface (`stocks`, `forex`, `reference`, `events`, `all`)
- `stocks.py`: S3 flat-file ingestion (`us_stocks_sip/day_aggs_v1`) into yearly Parquet files
- `forex.py`: REST `list_aggs` ingestion for `*USD` FX pairs into one Parquet file
- `reference.py`: REST ingestion for tickers/exchanges/splits/dividends plus ticker-event security master
- `checkpoint.py`: resumable download tracking via JSON checkpoint files
- `config.py`: download-specific constants (S3 endpoint/bucket, output `data/` path, REST rate-limit setting)

Output contract (read by other modules):
- `data/stocks/daily/{year}.parquet`
- `data/forex/daily_usd.parquet`
- `data/reference/{tickers,exchanges,splits,dividends,ticker_events}.parquet`

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
