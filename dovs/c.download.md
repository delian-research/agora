# Historical Data Download System

## Overview

The `agora.download` module downloads historical market data from Massive.com (formerly Polygon.io) and stores it as local Parquet files. It uses two data channels:

1. **S3 Flat Files** — Bulk CSV downloads for US stock daily OHLCV (no rate limit, ~3 min for 5 years)
2. **REST API** — Per-ticker queries for forex, reference data, and ticker events

All downloads are **resumable** via JSON checkpoint files. Interrupted downloads can be continued by re-running the same command.

---

## Quick Start

```bash
# Download everything (stocks, forex, reference, ticker events)
python -m agora.download all

# Or run individual steps
python -m agora.download stocks
python -m agora.download forex
python -m agora.download reference
python -m agora.download events

# Sync the live security master (current snapshot + append-only change log)
python -m agora.download security-master
```

### CLI Options

```
python -m agora.download [-o OUTPUT] [-v] [--no-resume] {stocks,forex,reference,events,security-master,all}

Options:
  -o, --output DIR     Output directory (default: ./data)
  -v, --verbose        Verbose/debug logging
  --no-resume          Ignore checkpoints, re-download everything

Subcommand options:
  stocks --start-year YYYY --end-year YYYY    (default: 2021–2026)
  security-master --no-events                 Skip ticker_events backfill on adds/changes
                  --full-event-backfill       Pull events for every identity (one-shot)
                  --allow-partial             Continue if a list_tickers segment fails
                  --no-snapshot               Skip the dated raw-pull snapshot
```

---

## Data Sources & API Details

### Massive.com (Polygon.io)

Massive.com is the rebranded Polygon.io. The Python SDK is `massive` (pip package), which wraps their REST and WebSocket APIs. The SDK auto-paginates list endpoints.

**Authentication:**
- REST API: `MASSIVE_API_KEY` env var (loaded from `.env`)
- S3 Flat Files: Separate access key ID + the same API key as secret key

**SDK:**
```python
from massive import RESTClient
client = RESTClient(api_key="...")
```

### Subscription: Stocks Starter + Currencies Basic + Indices Basic

| Feature | Stocks | Forex/Crypto | Indices |
|---------|--------|-------------|---------|
| Rate limit | Unlimited (performance only) | 5 calls/min | 5 calls/min |
| History (REST API) | ~5 years rolling | ~2 years rolling | 1+ years |
| History (Flat Files) | ~5 years (S3 download) | 403 Forbidden | 403 Forbidden |
| Flat file access | Yes | No | No |

### S3 Flat Files

Flat files are daily gzipped CSVs hosted on an S3-compatible endpoint. They provide the most efficient way to bulk-download stock data — no rate limit, just network bandwidth.

**Connection details:**
```python
import boto3
from botocore.config import Config

session = boto3.Session(
    aws_access_key_id="<MASSIVE_S3_ACCESS_KEY_ID>",  # per-account; from your Massive dashboard
    aws_secret_access_key="<MASSIVE_API_KEY>",       # same as REST API key
)
s3 = session.client(
    "s3",
    endpoint_url="https://files.massive.com",
    config=Config(signature_version="s3v4"),
)
```

**Bucket:** `flatfiles`

**Available prefixes:**
```
flatfiles/
├── us_stocks_sip/        ← US stocks (SIP feed)
│   ├── day_aggs_v1/      ← Daily OHLCV (what we use)
│   ├── minute_aggs_v1/
│   ├── trades_v1/
│   └── quotes_v1/
├── global_forex/         ← Forex (403 on our plan)
├── global_crypto/        ← Crypto (403 on our plan)
├── us_indices/           ← Indices (403 on our plan)
├── us_options_opra/
└── us_futures_*/
```

**File path pattern:** `{prefix}/day_aggs_v1/{YYYY}/{MM}/{YYYY-MM-DD}.csv.gz`

**Example:** `us_stocks_sip/day_aggs_v1/2024/01/2024-01-02.csv.gz`

---

## Output Files

### Directory Layout

```
data/
├── stocks/daily/
│   ├── 2021.parquet          38.9 MB   1,853,950 rows
│   ├── 2022.parquet          59.4 MB   2,795,047 rows
│   ├── 2023.parquet          58.4 MB   2,663,345 rows
│   ├── 2024.parquet          59.9 MB   2,665,129 rows
│   ├── 2025.parquet          65.8 MB   2,814,320 rows
│   ├── 2026.parquet          23.1 MB     937,656 rows  (partial year)
│   └── .checkpoint.json
├── forex/
│   ├── daily_usd.parquet      2.7 MB      69,375 rows
│   └── .checkpoint.json
└── reference/
    ├── tickers.parquet        0.7 MB      13,715 rows
    ├── exchanges.parquet      0.0 MB          52 rows
    ├── splits.parquet         0.3 MB      27,541 rows
    ├── dividends.parquet     18.1 MB   1,988,816 rows
    ├── ticker_events.parquet  0.4 MB      11,022 rows
    └── .events_checkpoint.json
```

### Schema: `stocks/daily/{year}.parquet`

Daily OHLCV for all US equities (~12,000–13,000 tickers per day).

| Column | Type | Description |
|--------|------|-------------|
| `date` | datetime | Trading date |
| `ticker` | string | Ticker symbol (e.g., AAPL) |
| `open` | float64 | Opening price |
| `high` | float64 | High price |
| `low` | float64 | Low price |
| `close` | float64 | Closing price |
| `volume` | float64 | Shares traded |
| `trades` | int64 | Number of transactions |

**Source:** S3 flat files (`us_stocks_sip/day_aggs_v1`). Prices are **not adjusted** for splits — they are raw exchange prices. The flat files CSV column `window_start` is a nanosecond epoch timestamp converted to `date` at download time.

**Coverage:** April 2021 → present (~5-year rolling window based on subscription).

### Schema: `forex/daily_usd.parquet`

Daily OHLCV for 116 foreign-currency-to-USD pairs.

| Column | Type | Description |
|--------|------|-------------|
| `date` | datetime | Trading date |
| `ticker` | string | Pair symbol (e.g., C:EURUSD) |
| `open` | float64 | Opening rate |
| `high` | float64 | High rate |
| `low` | float64 | Low rate |
| `close` | float64 | Closing rate |
| `volume` | int64 | Volume |
| `trades` | int64 | Number of transactions |

**Source:** REST API `list_aggs()`. Only `*USD` pairs are downloaded (116 pairs). The ticker format is `C:XXXUSD` where XXX is the foreign currency ISO code.

**Coverage:** ~2 years rolling based on subscription.

**Rate limit:** 5 calls/min (12 seconds between calls). Full download takes ~23 minutes.

### Schema: `reference/tickers.parquet`

All active ticker reference data for stocks and forex.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | string | Ticker symbol |
| `name` | string | Full company/instrument name |
| `market` | string | `stocks` or `fx` |
| `type` | string | Ticker type code (see below) |
| `locale` | string | Locale (e.g., `us`) |
| `active` | bool | Whether currently active |
| `currency_name` | string | Trading currency |
| `composite_figi` | string | Bloomberg Composite FIGI |
| `share_class_figi` | string | Bloomberg Share Class FIGI |
| `cik` | string | SEC CIK number |
| `primary_exchange` | string | Primary exchange MIC code |
| `last_updated_utc` | string | Last update timestamp |
| `source_feed` | object | Data source feed |

**Ticker type codes (stocks):**

| Code | Description |
|------|-------------|
| `CS` | Common Stock |
| `ETF` | Exchange Traded Fund |
| `PFD` | Preferred Stock |
| `WARRANT` | Warrant |
| `ADRC` | ADR Common |
| `FUND` | Fund |
| `UNIT` | Unit |
| `SP` | Structured Product |
| `ETS` | Single-security ETF |
| `ETN` | Exchange Traded Note |
| `RIGHT` | Rights |
| `ETV` | Exchange Traded Vehicle |

**Source:** REST API `list_tickers()` with auto-pagination.

### Schema: `reference/exchanges.parquet`

US exchange reference data.

| Column | Type | Description |
|--------|------|-------------|
| `id` | int64 | Polygon exchange ID |
| `mic` | string | Market Identifier Code (ISO 10383) |
| `operating_mic` | string | Operating MIC |
| `name` | string | Exchange name |
| `type` | string | `exchange` or `TRF` |
| `asset_class` | string | Asset class (e.g., `stocks`) |
| `locale` | string | Locale |
| `acronym` | string | Exchange acronym |
| `participant_id` | string | SIP participant ID |
| `url` | string | Exchange website URL |

**Source:** REST API `get_exchanges()` — single call, 52 exchanges.

### Schema: `reference/splits.parquet`

All stock splits (full history, no date restriction).

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | string | Ticker symbol |
| `execution_date` | datetime | Split execution date |
| `split_from` | float64 | Pre-split share count |
| `split_to` | float64 | Post-split share count |

**Example:** AAPL 4:1 split on 2020-08-31 → `split_from=1, split_to=4`

**Source:** REST API `list_splits()` with auto-pagination.

### Schema: `reference/dividends.parquet`

All stock dividends (full history).

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | string | Ticker symbol |
| `ex_dividend_date` | datetime | Ex-dividend date |
| `pay_date` | datetime | Payment date |
| `record_date` | datetime | Record date |
| `declaration_date` | datetime | Declaration date |
| `cash_amount` | float64 | Dividend amount per share |
| `currency` | string | Payment currency (e.g., `USD`) |
| `frequency` | int64 | Annual frequency (4=quarterly, 12=monthly, etc.) |
| `dividend_type` | string | Type code (`CD`=cash dividend, etc.) |

**Source:** REST API `list_dividends()` with auto-pagination. This is the largest reference download (~2M rows, takes ~15 min).

### Schema: `reference/ticker_events.parquet` (Security Master)

Historical record of ticker symbol changes. Each row represents a ticker identity at a point in time, linked by `composite_figi` as the stable identifier.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | string | Ticker symbol during this period |
| `composite_figi` | string | Stable security identifier (Bloomberg FIGI) |
| `cik` | string | SEC CIK number |
| `name` | string | Current security name |
| `valid_from` | datetime | Date this ticker became active |
| `valid_to` | datetime | Date this ticker was replaced (NaT if current) |
| `is_current` | bool | Whether this is the current ticker |

**Example — META (formerly FB):**
```
ticker  composite_figi  valid_from  valid_to    is_current
FB      BBG000MM2P62    2012-05-18  2022-06-09  False
META    BBG000MM2P62    2022-06-09  NaT         True
```

**Using the security master:** To join price data to a stable identity:
```python
# Get full price history for a security regardless of ticker changes
events = pd.read_parquet("data/reference/ticker_events.parquet")
prices = pd.read_parquet("data/stocks/daily/2022.parquet")

merged = prices.merge(events, on="ticker")
merged = merged[
    (merged["date"] >= merged["valid_from"]) &
    ((merged["date"] < merged["valid_to"]) | merged["valid_to"].isna())
]
# Now group by composite_figi for a continuous time series
```

**Source:** REST API `get_ticker_events()` per ticker. Scoped to tickers appearing in downloaded price data, filtered to CS + ETF types (~10,167 tickers). No rate limit on this endpoint — runs at ~18 calls/sec, completes in ~8 minutes.

### Schema: `reference/security_master.parquet` (Live Master)

Current state of every security identity in scope. One row per identity,
keyed on `composite_figi` (Polygon's stable Bloomberg FIGI). For
identities without a FIGI (indices, some fx pairs), falls back to
`ticker` as identity and flags via `identity_source = 'ticker'`.

| Column | Type | Notes |
|--------|------|-------|
| `composite_figi` | string | Primary identity (null for indices/fx → ticker fallback) |
| `share_class_figi` | string | Share-class FIGI |
| `ticker` | string | Current ticker symbol |
| `name` | string | Long name |
| `cik` | string | SEC CIK |
| `market` | string | `stocks` / `fx` / `indices` |
| `type` | string | `CS` / `ETF` / `ADRC` / etc. (null for indices) |
| `locale` | string | `us` |
| `primary_exchange` | string | MIC code |
| `currency_name` | string | Reporting currency |
| `active` | bool | Currently active in Massive's universe |
| `polygon_last_updated_utc` | string | Polygon's own last-modified timestamp |
| `first_seen_at` | tz datetime | UTC timestamp of first sync that detected this identity |
| `last_seen_at` | tz datetime | UTC timestamp of most recent sync that confirmed it |
| `as_of` | tz datetime | This snapshot's timestamp |
| `identity_source` | string | `composite_figi` (canonical) or `ticker` (fallback) |

**Universe scope:**

| Market | Filter | Typical row count |
|--------|--------|------|
| `stocks` | Types: CS, ETF, ADRC, ADRP, ETN, ETV, FUND, SP, RIGHT, WARRANT | ~11,800 |
| `fx` | USD-quoted pairs only (`*USD` suffix) | ~116 |
| `indices` | Allowlist from `data/indices_included.csv` | ~318 |

The allowlist file is a CSV with at minimum a `ticker` column. Edit it
to control which indices are tracked — no code change required. Missing
or malformed CSV → empty allowlist (warned, not fatal).

**Source:** Per-(market, type) `list_tickers(active=True)` calls. Each
sync overwrites this file atomically (write to `.tmp` → `os.replace`).

### Schema: `reference/security_master_changes.parquet` (Append-Only Log)

Every change detected across syncs. New rows are appended; never
modified or deleted. One UUID `run_id` per sync invocation groups all
changes from that run.

| Column | Type | Notes |
|--------|------|-------|
| `change_id` | string | UUID4 |
| `composite_figi` | string | Identity affected |
| `ticker` | string | Ticker at time of change (denormalized for grep) |
| `change_type` | string | See enum below |
| `field_name` | string | Affected field for `field_changed`, else null |
| `old_value` | string | Stringified prior value |
| `new_value` | string | Stringified new value |
| `event_date` | string | `YYYY-MM-DD` for renames (Polygon's authoritative date) |
| `detected_at` | tz datetime | UTC timestamp when *we* observed the change |
| `source` | string | `bootstrap` / `list_tickers` / `ticker_events_cache` / `ticker_events_api` |
| `run_id` | string | UUID grouping all changes from one sync |

**`change_type` enum:**

| Type | Triggered by |
|------|---------|
| `added` | Identity appeared in the universe for the first time |
| `deactivated` | Was active in prior master, absent from today's pull |
| `reactivated` | Was inactive, appears active today |
| `field_changed` | A watched field changed value (`ticker`, `name`, `primary_exchange`, `type`, `locale`, `currency_name`, `share_class_figi`, `cik`) |
| `ticker_renamed` | Polygon's ticker_events API confirmed a rename event with an authoritative `event_date` |

`field_changed` records what *we* observed (when we observed it).
`ticker_renamed` carries Polygon's actual `event_date`, which can predate
our first detection by years. Both are emitted on detected ticker
renames so analysis can answer either "when did we know" or "when did
it actually change."

**Identity model:** `composite_figi` is the canonical key. Two tickers
that share a FIGI (e.g. `FB` → `META`) are the same identity. A ticker
that has been used by multiple distinct securities over time (e.g.
`META` was Roundhill's ETF before Facebook) produces separate rows
distinguished by `composite_figi`.

**Dedupe semantics:** `ticker_renamed` rows are deduped on
`(composite_figi, event_date, new_value)` both within a single run and
against the existing log. Re-running `--full-event-backfill` is
idempotent — historical events already in the log are skipped.

### `reference/snapshots/tickers_<YYYY-MM-DD>.parquet`

Optional dated raw snapshot of the universe pull. One file per sync
date. Useful for retrospective "what did the universe look like on
2026-05-10" questions and for replaying a diff later. Skip with
`--no-snapshot` if disk is tight.

### Production semantics

- **Atomic writes**: master, change log, and snapshots all write via
  `<path>.tmp` + `os.replace`. A killed process leaves the prior file
  intact — never half-written or corrupted.
- **Strict universe by default**: any failed `list_tickers` segment
  raises `PartialUniverseError`. Without this, a transient API blip
  on (e.g.) the CS pull would leave thousands of stocks absent from
  today's universe — the diff layer would then emit false
  `deactivated` rows for all of them. Use `--allow-partial` only if
  you've separately verified the impact is acceptable.
- **Cache-first event lookup**: a precomputed `_EventCache` over
  `ticker_events.parquet` short-circuits ~80% of API calls on the
  daily run. Lookups are O(1) (dict-backed).
- **Event date normalization**: `event_date` is always
  `YYYY-MM-DD`, regardless of whether it came from the cache (a
  `Timestamp`) or the API (an ISO datetime string). Ensures the
  dedupe key is stable across runs and sources.

### Recommended bootstrap sequence

```bash
# 1) Populate the events cache (fills ticker_events.parquet for tickers
#    in your price data — ~8 min at unlimited rate).
python -m agora.download events

# 2) Bootstrap the master with full historical rename context (cache-fed,
#    fast — ~5 min).
python -m agora.download security-master --full-event-backfill

# 3) Daily ongoing — incremental diffs only, ~20-30s with warm cache.
python -m agora.download security-master
```

---

## Architecture

### Module Structure

```
agora/download/
├── __init__.py        Exports: download_stocks, download_forex, download_reference, …
├── __main__.py        python -m agora.download entrypoint
├── cli.py             Argument parsing, subcommands; main(argv, ...) -> int
├── config.py          S3 credentials, data directory, rate limits
├── checkpoint.py      JSON-based resume tracking
├── result.py          DownloadResult dataclass (return type for every download_*)
├── metrics.py         download_metrics() context manager (timing + counters)
├── stocks.py          S3 flat file downloader → yearly Parquet
├── forex.py           REST API downloader → single Parquet
└── reference.py       Tickers, exchanges, splits, dividends, ticker events
```

### Return type: `DownloadResult`

Every `download_*` function returns a structured
:class:`agora.download.result.DownloadResult` rather than a plain `Path`.
The dataclass exposes:

| Field | Description |
|---|---|
| `stage` | Identifier (e.g. `"download_stocks"`) |
| `output_dir` | Where files were written |
| `rows_written` / `files_written` / `bytes_written` | Output volume |
| `duration_seconds`, `started_at`, `finished_at` | Wall-clock timing |
| `requested` / `completed` / `skipped` / `failed` | Work-unit accounting (years for stocks; tickers for forex/events) |
| `warnings` | Free-form notes |
| `checkpoint_path` | The checkpoint file used, if any |
| `succeeded` (property) | `True` iff `failed` is empty |

Each downloader wraps its body in a `download_metrics(...)` context
manager which stamps `started_at` / `finished_at` / `duration_seconds`
and emits one structured `logger.info` summary line when the operation
finishes — so cron logs are greppable for ops.

Recommended cron-side persistence (delian): wrap the call and persist
`result.to_dict()` to `data_quality.download_runs` for trend analysis::

    from agora.download import download_stocks

    result = download_stocks()
    if not result.succeeded:
        alert(f"download_stocks lost {len(result.failed)} years: {result.failed}")
    persist_run_record(result.to_dict())  # JSON-serializable

### Design Decisions

**Flat files for stocks, REST API for forex:** The flat files approach downloads all ~10K+ tickers for a given day in a single S3 GET — no rate limit, purely bandwidth-bound. For stocks this is ~1,252 files for 5 years, completing in ~3 minutes. Forex and indices flat files return 403 Forbidden on our plan, so those use the REST API with rate limiting.

**Yearly Parquet organization:** Each year of stock data is one Parquet file. This balances query flexibility (Parquet predicate pushdown filters by date or ticker efficiently) against file count. A single file per year is small enough to load into memory (~60 MB) but large enough to avoid file bloat.

**Checkpoint-based resume:** Each download step writes a JSON checkpoint file (`.checkpoint.json`) tracking completed items. Stocks checkpoint by year; forex checkpoints by ticker; events checkpoint by ticker. Re-running a command skips completed work automatically. Use `--no-resume` to force re-download.

---

## Gotchas & Lessons Learned

### 1. Flat Files: Can List but Can't Download

S3 `list_objects_v2` returns files across all years (2003–2026) regardless of subscription. But `get_object` returns **403 Forbidden** for files outside your plan's history window. We discovered this by getting file listings for 2003 data that was actually inaccessible.

**Impact:** The stock downloader silently skips 403 errors per file. For 2021 specifically, files before May are blocked, so `2021.parquet` starts at April 27, 2021 (174 trading days instead of ~252).

### 2. S3 Auth Uses Your REST API Key as the Secret

The S3 flat files endpoint (`files.massive.com`) uses a **separate access key ID** but your **REST API key as the secret**. The access key ID is visible in your Massive dashboard under "Flat Files" credentials. This is not documented in the SDK — you must use `boto3` directly with `signature_version='s3v4'`.

### 3. Forex/Crypto/Indices Flat Files Are Blocked

Even though `list_objects_v2` returns file metadata for `global_forex/`, `global_crypto/`, and `us_indices/` prefixes, downloading any file returns 403. These flat files require higher subscription tiers. We fall back to the REST API for forex.

### 4. Stock Flat File Prices Are NOT Split-Adjusted

The flat file daily aggregates contain **raw exchange prices**, not split-adjusted prices. This differs from the REST API's `list_aggs()` which returns adjusted prices by default (`adjusted=True`). If you need adjusted prices, apply the splits from `reference/splits.parquet` yourself.

### 5. REST API Rate Limits Vary by Endpoint Type

| Endpoint Category | Rate Limit |
|-------------------|-----------|
| Stock reference (tickers, events, splits, dividends) | Unlimited (performance-bound, ~18 calls/sec) |
| Stock aggregates (per-ticker OHLCV) | Unlimited |
| Forex aggregates | 5 calls/min |
| Index aggregates | 5 calls/min |

The ticker events download originally had a 12-second sleep between calls (inheriting the forex rate limit), which would have taken ~34 hours. Removing it brought the time down to ~8 minutes.

### 6. `list_aggs` vs `get_aggs`

The SDK has two aggregate methods:
- `list_aggs()` — Returns an **iterator** with automatic pagination. Use this for large date ranges.
- `get_aggs()` — Returns a **list** (loads all results into memory). Limited to `limit` parameter (max 50,000).

We use `list_aggs()` for forex to ensure we get all data across the 2-year range without worrying about pagination.

### 7. Flat File Timestamps Are Nanosecond Epochs

The CSV `window_start` column is a **nanosecond** epoch timestamp (e.g., `1767330000000000000`), not millisecond like the REST API's `timestamp` field. Pass `unit="ns"` to `pd.to_datetime()` for flat files, `unit="ms"` for REST API responses.

### 8. Dividends Download Is Slow (~15 min)

The `list_dividends()` endpoint with auto-pagination downloads ~2 million rows. The SDK paginates automatically but each page requires an API call. This is the slowest part of the reference download. There's no way to speed it up without higher-tier API access.

### 9. 2023 Grouped Daily Returns 0 Tickers for Weekends/Holidays

When testing `get_grouped_daily_aggs("2023-06-03")` (a Saturday), the API returns 0 results with no error. Always use actual trading dates when querying grouped daily data. The flat file approach avoids this since files only exist for trading days.

### 10. Ticker Events Only Track Symbol Changes

The `get_ticker_events()` endpoint returns `ticker_change` events. It does **not** track name changes, exchange transfers, or other corporate actions. For a complete corporate action history, combine `ticker_events.parquet` with `splits.parquet` and `dividends.parquet`.

### 11. The `massive` SDK Is a Renamed `polygon` SDK

Polygon.io rebranded to Massive.com. The Python package was renamed from `polygon` to `massive`, but the API domain `api.polygon.io` still works (redirects to `api.massive.com`). The SDK reads `MASSIVE_API_KEY` from env (previously `POLYGON_APIKEY`). All REST endpoints use the same URL structure.

---

## Updating Data

The download system is designed for periodic re-runs:

```bash
# Re-run to update current year (2026) only
# Previous years are skipped via checkpoint
python -m agora.download stocks --start-year 2026 --end-year 2026 --no-resume

# Re-run forex (skips already-downloaded tickers via checkpoint)
python -m agora.download forex

# Full refresh of reference data
python -m agora.download reference
```

For the current year's stock data, use `--no-resume` to re-download the full year file (since new trading days have been added). Previous years are immutable and don't need re-downloading.

---

## Performance

Measured on the initial download (April 2026):

| Step | Method | Time | Output Size |
|------|--------|------|-------------|
| Stocks (2021–2026) | S3 flat files | ~3 min | 306 MB |
| Forex (116 USD pairs) | REST API @ 5/min | ~23 min | 2.7 MB |
| Reference (tickers, exchanges, splits, divs) | REST API | ~15 min | 19 MB |
| Ticker Events (10,167 CS+ETF) | REST API (unlimited) | ~8 min | 0.4 MB |
| **Total** | | **~49 min** | **328 MB** |
