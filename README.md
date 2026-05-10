# agora

[![test](https://github.com/delian-research/agora/actions/workflows/test.yml/badge.svg)](https://github.com/delian-research/agora/actions/workflows/test.yml)

Market-data ingestion and local Parquet store for [Massive.com](https://massive.com)
(formerly Polygon.io). Bulk historical via S3 flat files, live REST + WebSocket
access, security master with FIGI-linked ticker history.

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  Massive.com                                          Local Parquet  │
│  ┌──────────────┐                                  ┌──────────────┐  │
│  │  S3 flat     │──┐    agora/download (bulk)      │ data/        │  │
│  │  files       │  ├────────────────────────────►  │   stocks/    │  │
│  └──────────────┘  │                                │   forex/     │  │
│  ┌──────────────┐  │                                │   reference/ │  │
│  │  REST API    │──┤    agora/loaders/rest          └──────┬───────┘  │
│  └──────────────┘  │       (live + retry)                  │          │
│  ┌──────────────┐  │                                       ▼          │
│  │  WebSocket   │──┤    agora/loaders/socket    agora/loaders/parquet │
│  │  feed        │  │       (live stream)         (read; FlatFileLoader)│
│  └──────────────┘  │                                                  │
│                    │                                                  │
│                    └─►  agora/client.MassiveClient (orchestrator)     │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

## Features

- **`agora.equities` — API-first domain helpers** for prices, returns,
  volume, snapshots, dividends, splits, financial statements, short
  data, ETF holdings/flows, and reference catalogs. Thin wrapper over
  the Massive REST API; downstream packages own caching/storage.
- **Bulk historical download** — ~5 years of US stock daily OHLCV (~13M rows)
  via S3 flat files in ~3 minutes; ~2 years of forex (XXX→USD) via REST in
  ~23 minutes; tickers, exchanges, splits, dividends, and a security master.
- **Resumable** — JSON-checkpointed downloads. Re-running the CLI skips
  completed work automatically.
- **Three retrieval modes**: `MassiveDataApi` (live REST with retry/backoff),
  `FlatFileLoader` (offline Parquet, no rate limit), `WebSocketStreamer`
  (live trades / quotes / aggregates).
- **Security master** — `composite_figi`-linked ticker history; resolves
  symbol changes (FB → META) and stitches continuous price series across
  renames.
- **Live security master + change log** — incremental sync that produces
  a current-state snapshot (`security_master.parquet`) plus an
  append-only timestamped change log (`security_master_changes.parquet`).
  Captures additions, deactivations, reactivations, field changes, and
  authoritative ticker renames. Idempotent; safe to run on cron.

## Install

Requires **Python 3.13+** and a Massive.com API key.

```bash
# Clone & install (with uv)
git clone https://github.com/<user>/agora.git
cd agora
uv sync

# Or with pip
pip install -e .

# Set up your API key
cp .env.example .env
# edit .env and set MASSIVE_API_KEY=...
```

For development:

```bash
uv sync --extra dev          # adds pytest + pytest-mock
pytest                        # runs the smoke suite
```

## Contributing workflow

Branches:

- **`dev`** — default branch; integration target for all feature work.
- **`main`** — stable / released code; only updated by promoting `dev`.

Feature flow (`local → dev`):

```bash
git switch dev
git pull
git switch -c feat/my-thing
# ... edit, commit ...
git push -u origin feat/my-thing
gh pr create --fill        # base defaults to dev
```

CI (the pytest suite, ~260 tests) runs on every PR and on every push to
`dev` or `main`. Branch protection on `dev` requires the `test` check
to pass before the merge button enables. Direct pushes to either
protected branch are blocked.

Release flow (`dev → main`):

```bash
gh pr create --base main --head dev --title "Release"
# review, merge once dev is stable
```

## Quickstart

### Bulk download

```bash
# Download everything (stocks + forex + reference + ticker events)
python -m agora.download all

# Or run individual steps
python -m agora.download stocks
python -m agora.download forex
python -m agora.download reference
python -m agora.download events

# Sync the live security master + append timestamped change log
python -m agora.download security-master

# Re-download from scratch (ignore checkpoints)
python -m agora.download --no-resume all
```

Result lands under `data/`:

```
data/
├── indices_included.csv                     # editable allowlist for index scope
├── stocks/daily/{2021..2026}.parquet        # ~306 MB, ~13.7M rows
├── forex/daily_usd.parquet                  # ~3 MB, 116 *USD pairs
└── reference/
    ├── tickers.parquet                      # raw list_tickers snapshot
    ├── exchanges.parquet
    ├── splits.parquet
    ├── dividends.parquet                    # ~2M rows
    ├── ticker_events.parquet                # rename history (FIGI bridge)
    ├── security_master.parquet              # current state, one row per identity
    ├── security_master_changes.parquet      # append-only timestamped change log
    └── snapshots/tickers_<YYYY-MM-DD>.parquet  # dated raw-pull archive
```

### Security master sync

```bash
# One-shot: pull events for every identity (use after the bootstrap)
python -m agora.download security-master --full-event-backfill

# Daily incremental — fast (~20-30s with warm cache), idempotent
python -m agora.download security-master
```

Edit `data/indices_included.csv` to control which indices are tracked
(must have a `ticker` column). The change log is append-only — each
sync that detects additions, deactivations, reactivations, field
changes, or authoritative renames appends rows tagged with the run's
UUID and `detected_at` timestamp.

```python
from agora import FlatFileLoader
loader = FlatFileLoader()

loader.get_security_master(active_only=True, ticker_type="ETF")
loader.audit_security("BBG000B9XRY4")            # full history for AAPL
loader.resolve_security(ticker="META", as_of="2020-06-01")  # → resolves to FB
```

### Equity domain helpers (API-first)

`agora.equities` is the recommended user-facing API. All helpers hit
the Massive REST API directly — there is no local-cache code path
inside this namespace. Layer your own cache on top via
`FlatFileLoader` if you need persistence.

```python
from agora import equities

# ── Market data ─────────────────────────────────────────────────────
prices  = equities.get_daily_prices(["AAPL", "MSFT"], period="1y")
returns = equities.get_daily_returns(["SPY"], period="2y", method="log")
volume  = equities.get_volume(["AAPL"], period="3mo")
snap    = equities.get_snapshot(["AAPL", "MSFT", "NVDA"])
prev    = equities.get_previous_close(["AAPL", "MSFT"])
trade   = equities.get_last_trade("AAPL")
quote   = equities.get_last_quote("AAPL")
status  = equities.get_market_status()
hols    = equities.get_market_holidays()

# Cross-section: all tickers for one date (one bulk call)
day = equities.get_daily_grouped("2024-01-03")

# ── Reference / catalogs ───────────────────────────────────────────
universe   = equities.get_tickers(market="stocks", type="CS")
profile    = equities.get_ticker_details(["AAPL", "MSFT"])
exchanges  = equities.get_exchanges()
types_     = equities.get_ticker_types()
related    = equities.get_related_tickers("AAPL")

# ── Corporate actions ───────────────────────────────────────────────
divs   = equities.cax.get_dividends("AAPL")
splits = equities.cax.get_splits("AAPL")

# ── Fundamentals ────────────────────────────────────────────────────
income = equities.fundamentals.get_income_statements("AAPL", timeframe="annual")
bs     = equities.fundamentals.get_balance_sheets("AAPL", timeframe="quarterly")
cf     = equities.fundamentals.get_cash_flow_statements("AAPL")
ratios = equities.fundamentals.get_ratios("AAPL")

# ── Short data ──────────────────────────────────────────────────────
si     = equities.short_data.get_short_interest("AAPL")
sv     = equities.short_data.get_short_volume("AAPL", start="2024-01-01")
floats = equities.short_data.get_floats("AAPL")

# ── ETF data ────────────────────────────────────────────────────────
holdings = equities.etf.get_constituents("SPY")
flows    = equities.etf.get_fund_flows("SPY", start="2024-01-01")
profile  = equities.etf.get_profiles("SPY")
analytics = equities.etf.get_analytics("SPY")
taxonomy = equities.etf.get_taxonomies("SPY")

# ── Classification ──────────────────────────────────────────────────
industry = equities.get_industry(["AAPL", "JPM", "XOM"])
sector   = equities.get_sector(["AAPL", "JPM", "XOM"])
```

### Read local Parquet (fast, no rate limits)

```python
from agora import FlatFileLoader

loader = FlatFileLoader()

# Pivoted price matrix (date × ticker)
prices = loader.get_prices(["AAPL", "MSFT", "NVDA"], start="2024-01-01")

# FX rate matrix by ISO code
rates = loader.get_fx_rates(["EUR", "GBP", "JPY"])

# Continuous price series across symbol changes
meta_history = loader.get_continuous_prices("META", start="2021-06-01")
# Includes data under both FB and META symbols, no gaps
```

### Live REST API

```python
from agora import MassiveClient

with MassiveClient.from_env() as c:
    aggs = c.rest.get_aggregates(
        "AAPL", 1, "day", "2024-01-01", "2024-12-31"
    )
    snapshot = c.rest.get_snapshot("AAPL")
```

### Live WebSocket streaming

```python
from agora import WebSocketStreamer

streamer = WebSocketStreamer(market="stocks")
streamer.subscribe_trades("AAPL", "MSFT")
streamer.subscribe_minute_aggs("SPY")

@streamer.on_message
def handle(msg):
    print(msg.event_type, msg)

streamer.run(timeout=60)   # auto-stop after 60s; or omit for Ctrl+C
```

## Module map

| Module | Purpose |
|---|---|
| `agora.equities` | **Recommended user-facing API** — domain helpers for prices, returns, volume, dividends, splits, snapshots. See `dovs/d.equities.md`. |
| `agora.client.MassiveClient` | Orchestrator — `from_env()` builds a config-bound client with `.rest`, `.flat_files()`, `.ws_streamer()` |
| `agora.config.MassiveConfig` | Env-loaded config (`MASSIVE_API_KEY`, base URL, timeout, retries) |
| `agora.loaders.rest.MassiveDataApi` | Live REST wrapper with retry/backoff |
| `agora.loaders.parquet.FlatFileLoader` | Read-only local Parquet access |
| `agora.loaders.socket.WebSocketStreamer` | Live trades/quotes/aggregates with verb-based subscribe API |
| `agora.download` | Bulk download CLI + library |
| `agora.normalize` | Payload → DataFrame transforms (used by older REST flows) |
| `agora.adapters.market` | **Deprecated.** Thin shims (`get_prices`, `get_returns`) that emit `DeprecationWarning` and forward to `agora.equities`. Use the equities namespace directly in new code. |

## Subscription tier notes

This package targets the **Stocks Starter + Currencies Basic + Indices Basic**
plan. Some boundaries to know:

- **Stocks**: ~5 years rolling history; effectively unlimited rate limit on
  reference endpoints (~18 calls/sec measured); flat-file S3 download is the
  fast path for bulk backfill.
- **Forex**: ~2 years rolling; **5 calls/min** rate limit; flat-file forex is
  blocked (downloader uses REST).
- **Indices**: aggregates blocked; flat-file indices blocked. Reference data
  available.
- **WebSocket**: real-time feed not included with Stocks Starter — the
  default `Feed.Delayed` (~15-min delay) is free with paid plans.

## Documentation

Long-form documentation lives under `dovs/`:

- `dovs/c.download.md` — bulk download pipeline (operational behavior, S3
  details, plan-tier gotchas)
- `dovs/b.loaders_doc.md` — three-loader walkthrough with method index
- `dovs/a.structure.md` — repository audit / module status
- `dovs/1.Updates.md` — change log
- `dovs/2.Projects.md` — project objectives

Operational guidance for AI coding agents: see `AGENTS.md`.

## License

Proprietary. See `pyproject.toml` `license` field.
