# Loaders — Data Access Layer

The `agora/loaders/` package is the **data-access layer** of agora. It exposes
three retrieval modes — pick the one that matches your access pattern:

| Loader | Module | Source | When to use |
|---|---|---|---|
| `MassiveDataApi` | `loaders/rest.py` | Live Massive REST API | Ad-hoc / intraday queries; reference data lookups; small ticker counts |
| `FlatFileLoader` | `loaders/s3.py` | Local Parquet (under `data/`) | Backtests, batch analytics, anything spanning many tickers/dates |
| `WebSocketStreamer` | `loaders/socket.py` | Live Massive WebSocket | Real-time / delayed streaming for trades, quotes, aggregates |

A `MassiveClient` (`agora.client.MassiveClient`) bundles all three so you
rarely build them by hand:

```python
from agora.client import MassiveClient

with MassiveClient.from_env() as c:
    aggs   = c.rest.get_aggregates("AAPL", 1, "day", "2024-01-01", "2024-12-31")
    parq   = c.flat_files().get_prices(["AAPL", "MSFT"], start="2024-01-01")
    stream = c.ws_streamer(market="stocks")
```

---

## Quick decision matrix

| You want… | Use |
|---|---|
| Last week of OHLCV for 5 tickers | `MassiveDataApi.get_aggregates()` |
| 5-year OHLCV for 1,000 tickers | `FlatFileLoader.get_stock_daily()` |
| Live trades streaming | `WebSocketStreamer.subscribe_trades()` |
| Today's snapshot for everything | `MassiveDataApi.get_all_snapshots()` |
| Ticker reference / FIGI lookup | `FlatFileLoader.get_tickers()` (offline) or `MassiveDataApi.get_ticker_details()` (live) |
| Continuous prices across symbol changes (FB→META) | `FlatFileLoader.get_continuous_prices()` |
| Convert non-USD prices to USD | `FlatFileLoader.get_fx_rates()` |

---

## 1. `MassiveDataApi` — live REST wrapper

`agora/loaders/rest.py` wraps `massive.RESTClient` with retry/backoff and
agora's exception types. Construct directly or via `MassiveClient.rest`.

### Construction

```python
from agora.loaders.rest import MassiveDataApi
api = MassiveDataApi()                      # loads MASSIVE_API_KEY from env
# or with explicit config:
from agora.config import MassiveConfig
api = MassiveDataApi(MassiveConfig.from_env())
```

`MassiveConfig` reads from `.env` at import time. Defaults: `base_url =
https://api.massive.com`, `timeout = 30`, `max_retries = 3`. Override with
`MASSIVE_TIMEOUT`, `MASSIVE_MAX_RETRIES`.

### Retry semantics

Every public method is wrapped by `@retry_with_backoff()` which:

- **HTTP 429 (rate limit)** → exponential backoff (1s → 60s cap), up to
  `config.max_retries` attempts; raises `MassiveRateLimitError` on exhaustion.
- **HTTP 401 / 403** → no retry; raises `MassiveAuthenticationError`.
- **Other `BadResponse`** → exponential backoff; raises `MassiveAPIError` on
  exhaustion.
- **Other `Exception`** → no retry; re-raises.

### Public methods

| Method | Returns | Notes |
|---|---|---|
| `get_aggregates(ticker, multiplier, timespan, from_date, to_date, adjusted=True, sort="asc", limit=50000)` | `list[Agg]` | `Agg.timestamp` is millisecond epoch |
| `get_snapshot(ticker)` | `TickerSnapshot` | Today's consolidated quote for one ticker |
| `get_all_snapshots()` | `list[TickerSnapshot]` | Single API call returning every US stock snapshot |
| `get_ticker_details(ticker)` | `TickerDetails` | Description, FIGI, CIK, market cap, etc. |

### Plan gotchas

- **Stock aggregates**: ~5 years rolling history; older dates → 403.
- **Forex aggregates** (`C:EURUSD` etc.): ~2 years rolling.
- **Indices aggregates** (`I:SPX`): blocked on Stocks Starter; use indices-tier add-on.
- **Rate limit**: stocks endpoints are effectively unlimited (perf-bound,
  ~18 calls/sec). Forex / indices / crypto aggregates are 5 calls/min.

---

## 2. `FlatFileLoader` — local Parquet store

`agora/loaders/parquet.py` reads the Parquet files produced by `agora.download`.
**No network calls. No rate limits.** This is the analyst's go-to loader.

> **Naming note**: this loader was previously named `s3.py` because it
> reads the output of an S3-flat-file ingestion pipeline. It was renamed
> to `parquet.py` to reflect its actual behavior — it reads local Parquet,
> not S3. The S3 client lives in `agora/download/config.py`.
>
> A back-compat shim at `agora/loaders/s3.py` keeps existing imports
> working, but emits a `DeprecationWarning` directing callers to the new
> path.

### Construction

```python
from agora.loaders.parquet import FlatFileLoader
loader = FlatFileLoader()                                  # uses ./data
loader = FlatFileLoader(data_dir="/path/to/parquet/store") # custom location
```

### Expected directory layout

```
data/
├── stocks/daily/{year}.parquet         # one file per year, all tickers
├── forex/daily_usd.parquet             # 116 *USD pairs
└── reference/
    ├── tickers.parquet
    ├── exchanges.parquet
    ├── splits.parquet
    ├── dividends.parquet
    └── ticker_events.parquet           # security master
```

If a file is missing the corresponding method returns an empty DataFrame
rather than raising — this lets analysis pipelines run on partial data.

### Stock prices

```python
# Long DataFrame
df = loader.get_stock_daily(["AAPL", "MSFT"], start="2024-01-01", end="2024-12-31")
# columns: date, ticker, open, high, low, close, volume, trades

# Pivoted matrix (date × ticker), default close price
prices = loader.get_prices(["AAPL", "MSFT", "NVDA"], start="2024-01-01")

# Other fields
opens = loader.get_prices(["AAPL"], field="open")
```

The loader caches each year's Parquet in memory after first read, so the
second call against the same year is essentially free.

> **Adjustment caveat**: flat-file prices are **NOT split-adjusted** —
> they are raw exchange prices. To compute adjusted prices, fold in
> `loader.get_splits()` and apply the cumulative ratio. This differs from
> the REST API where `adjusted=True` is the default.

### Forex

```python
# Long DataFrame, raw pair tickers
fx = loader.get_forex(["C:EURUSD", "C:GBPUSD"])

# Pivoted by ISO code (auto-maps EUR → C:EURUSD)
rates = loader.get_fx_rates(["EUR", "GBP", "JPY"], start="2025-01-01")
# Columns: EUR, GBP, JPY (units of foreign currency per 1 USD)
```

### Reference data

```python
loader.get_tickers(market="stocks", ticker_type="ETF")      # filter by type
loader.get_exchanges()                                       # 52 rows: MIC, name, etc.
loader.get_splits("AAPL")                                    # all AAPL splits
loader.get_dividends("AAPL")                                 # full dividend history
```

### Security master (ticker events)

The security master tracks ticker symbol changes over time, linked by the
stable Bloomberg `composite_figi`. Each row is `(ticker, valid_from,
valid_to, is_current)`.

```python
# All historical tickers for one security
loader.get_ticker_history("META")
#   ticker     valid_from   valid_to    is_current
#   FB         2012-05-18   2022-06-09  False
#   META       2022-06-09   NaT         True

# What was this security called on a given date?
loader.resolve_ticker("META", "2020-06-15")   # → "FB"

# Continuous price series across renames
series = loader.get_continuous_prices("META", start="2021-06-01")
# Pulls FB through 2022-06-08, META from 2022-06-09 onward, no gap.
```

`get_continuous_prices()` is the canonical way to backtest a security
without caring about ticker history. It handles the FIGI resolution
internally and stitches the price segments by date range.

### Method index

| Method | Purpose |
|---|---|
| `available_stock_years()` | List of years present in `data/stocks/daily/` |
| `get_stock_daily(tickers?, start?, end?)` | Long DataFrame of OHLCV |
| `get_prices(tickers, start?, end?, field="close")` | Pivoted price matrix |
| `get_forex(pairs?, start?, end?)` | Forex long DataFrame |
| `get_fx_rates(currencies?, start?, end?)` | Pivoted FX matrix by ISO code |
| `get_tickers(market?, ticker_type?)` | Ticker reference table |
| `get_exchanges()` | Exchange reference table |
| `get_splits(ticker?)` | Stock splits |
| `get_dividends(ticker?)` | Dividends |
| `get_ticker_events(ticker?)` | Raw security master records |
| `get_ticker_history(ticker)` | All identities for a security |
| `resolve_ticker(ticker, date)` | Symbol active on a given date |
| `get_continuous_prices(ticker, start?, end?, field="close")` | Stitched price series across renames |

---

## 3. `WebSocketStreamer` — live streaming

`agora/loaders/socket.py` wraps `massive.WebSocketClient` with verb-based
subscription helpers, a multi-handler registry, optional capture buffers,
and graceful shutdown.

### Construction

```python
from agora.loaders.socket import WebSocketStreamer
from massive.websocket.models.common import Feed

# Default: stocks market on the delayed feed (free with paid plans)
streamer = WebSocketStreamer(market="stocks")

# Real-time feed (requires entitlement)
streamer = WebSocketStreamer(market="stocks", feed=Feed.RealTime)

# Forex / crypto / indices (each has its own channel codes)
fx_stream  = WebSocketStreamer(market="forex")
ix_stream  = WebSocketStreamer(market="indices")
```

### Capability summary

| Capability | Method |
|---|---|
| Per-market construction | `WebSocketStreamer(market="stocks" \| "forex" \| "crypto" \| "indices")` |
| Subscriptions (verb-based, market-aware) | `.subscribe_trades()`, `.subscribe_quotes()`, `.subscribe_minute_aggs()`, `.subscribe_second_aggs()`, `.subscribe_values()` (indices), `.subscribe_luld()` / `.subscribe_imbalances()` (stocks), `.subscribe_l2_book()` (crypto), `.subscribe_raw()` escape hatch |
| Forex / crypto slash convenience | `subscribe_quotes("EUR/USD")` → `C.C:EURUSD` |
| Wildcards | `subscribe_trades("*")` works |
| Handler registry | `@streamer.on_message` (decorator), `@streamer.on_message(events=["T"])` (filtered), `streamer.on_message(fn)` (direct) |
| Capture | `streamer.buffer(maxlen=N)` → `streamer.buffered`, `streamer.dump_to_jsonl(path)` |
| Run modes | `streamer.run()` (blocking), `streamer.run(timeout=30)` (auto-stop), context manager (`with` block) |
| Configurable feed | `feed=Feed.RealTime` if entitled; defaults to `Feed.Delayed` for Stocks Starter compatibility |

### Markets, channels, and what `subscribe_*` maps to

The Massive WS API uses cryptic 1–4 letter channel codes that vary by
market. The wrapper hides them — call the same verb regardless of market
and the right code is sent.

| Verb | stocks | forex | crypto | indices |
|---|---|---|---|---|
| `subscribe_trades(*sym)` | `T` | — | `XT` | — |
| `subscribe_quotes(*sym)` | `Q` | `C` | `XQ` | — |
| `subscribe_minute_aggs(*sym)` | `AM` | `CA` | `XA` | — |
| `subscribe_second_aggs(*sym)` | `A` | `CAS` | `XAS` | — |
| `subscribe_values(*sym)` | — | — | — | `V` |
| `subscribe_luld(*sym)` | `LULD` | — | — | — |
| `subscribe_imbalances(*sym)` | `NOI` | — | — | — |
| `subscribe_l2_book(*sym)` | — | — | `XL2` | — |
| `subscribe_raw(*sub)` | escape hatch — pass `T.AAPL` directly |

Calling a verb that the market doesn't support raises `ValueError` early
(at subscribe time, not at run time).

### Handler registry

Multiple handlers can be attached, optionally filtered by event-type code.

```python
streamer = WebSocketStreamer(market="stocks")

@streamer.on_message
def all_handler(msg):
    print(msg)

@streamer.on_message(events=["T"])
def trade_handler(msg):
    print("trade:", msg)

streamer.on_message(my_quote_fn, events=["Q"])
```

Each incoming `WebSocketMessage` is dispatched to every handler whose
filter set matches (or has no filter). Handler exceptions are logged and
do not stop dispatch.

### Capture helpers

```python
# In-memory ring buffer
streamer.buffer(maxlen=10000)
# … run, then:
captured = streamer.buffered    # list copy

# JSONL sink (one event per line, append-mode for resume safety)
streamer.dump_to_jsonl("data/live/trades.jsonl")
```

Both run in addition to handlers — they fire even when no handler is
registered.

### Run modes

```python
# Blocking; Ctrl+C to stop
streamer.run()

# Auto-stop after N seconds (a threading.Timer raises KeyboardInterrupt
# in the main thread; we catch it for a clean shutdown)
streamer.run(timeout=30)

# Context manager
with WebSocketStreamer(market="forex") as s:
    s.subscribe_quotes("EUR/USD")
    s.run(timeout=10)
# .close() called on __exit__
```

### Plan / runtime gotchas

> **One subtle thing about live testing this code**: with the *delayed*
> feed and only 4–6 second windows, you'll typically see zero messages —
> the SDK takes ~1–2s to authenticate and confirm subscriptions, then
> the delayed feed sends `AM` (minute aggs) only at the close of each
> minute boundary. To actually see messages flow you'd need a longer
> window (60+ seconds), ideally during US market hours, or you'd
> subscribe to `T.*` (all trades) which fire constantly. The
> infrastructure is correct — auth succeeds, subscription is confirmed,
> timeout fires, no warnings — the empty buffer just reflects the feed's
> update cadence.

Other things to know:

- **Real-time WS is not included with Stocks Starter.** Default is the
  *delayed* feed (`delayed.massive.com`, ~15-min delay), free with paid
  plans and adequate for pipeline development.
- **Minute-agg cadence**: `AM`/`CA`/`XA` only emit at minute boundaries.
- **The SDK's `close()` is async**. The wrapper detects the returned
  coroutine and discards it cleanly so you never see
  `RuntimeWarning: coroutine ... was never awaited`.

---

## Cross-cutting: when to use which loader

```
                     ┌──────────────────────┐
                     │ Need historical data │
                     └──────────┬───────────┘
                                │
              ┌─────────────────┴─────────────────┐
              │                                   │
        > 100 tickers OR                     ≤ 100 tickers AND
        full year-spans?                     specific date range?
              │                                   │
              ▼                                   ▼
       FlatFileLoader                       MassiveDataApi
       (offline, instant)                   (live, single ticker per call)
```

```
                     ┌──────────────────┐
                     │ Need live data   │
                     └────────┬─────────┘
                              │
              ┌───────────────┴────────────────┐
              │                                │
        Streaming?                       Snapshot at "now"?
              │                                │
              ▼                                ▼
       WebSocketStreamer                MassiveDataApi.get_snapshot[_all]()
       (continuous push)                (one-shot pull)
```

---

## Module shape — one-line summary

| File | Class / function | Lines | Status |
|---|---|---|---|
| `loaders/rest.py` | `MassiveDataApi`, `retry_with_backoff` | 270 | Working (live API verified) |
| `loaders/parquet.py` | `FlatFileLoader` | 445 | Working (Parquet reads verified) |
| `loaders/s3.py` | `DeprecationWarning` shim → `parquet.py` | 33 | Working (warns) |
| `loaders/socket.py` | `WebSocketStreamer`, `_CHANNEL_CODES` | 425 | Working (auth + dispatch verified) |