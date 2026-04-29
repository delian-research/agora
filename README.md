# agora

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

# Re-download from scratch (ignore checkpoints)
python -m agora.download --no-resume all
```

Result lands under `data/`:

```
data/
├── stocks/daily/{2021..2026}.parquet      # ~306 MB, ~13.7M rows
├── forex/daily_usd.parquet                 # ~3 MB, 116 *USD pairs
└── reference/
    ├── tickers.parquet                      # 13,715 active tickers
    ├── exchanges.parquet
    ├── splits.parquet
    ├── dividends.parquet                    # ~2M rows
    └── ticker_events.parquet                # security master
```

### Read local Parquet (fast, no rate limits)

```python
from agora.loaders.parquet import FlatFileLoader

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
from agora.client import MassiveClient

with MassiveClient.from_env() as c:
    aggs = c.rest.get_aggregates(
        "AAPL", 1, "day", "2024-01-01", "2024-12-31"
    )
    snapshot = c.rest.get_snapshot("AAPL")
```

### Live WebSocket streaming

```python
from agora.loaders.socket import WebSocketStreamer

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
| `agora.client.MassiveClient` | Orchestrator — `from_env()` builds a config-bound client with `.rest`, `.flat_files()`, `.ws_streamer()` |
| `agora.config.MassiveConfig` | Env-loaded config (`MASSIVE_API_KEY`, base URL, timeout, retries) |
| `agora.loaders.rest.MassiveDataApi` | Live REST wrapper with retry/backoff |
| `agora.loaders.parquet.FlatFileLoader` | Read-only local Parquet access |
| `agora.loaders.socket.WebSocketStreamer` | Live trades/quotes/aggregates with verb-based subscribe API |
| `agora.adapters.market` | High-level analytics helpers (`get_prices`, `get_returns`) |
| `agora.normalize` | Payload → DataFrame transforms (used by older REST flows) |
| `agora.download` | Bulk download CLI + library |

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
