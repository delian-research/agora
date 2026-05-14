"""Equity-domain API.

This is the user-facing layer for US equities. It speaks in terms of
"prices, returns, volume, snapshots, dividends, splits, classification" —
not "REST endpoints" or "Parquet paths."

Public surface (a "verb-shape" list):

    Market
        get_daily_prices    — per-ticker OHLCV time-series via REST
        get_daily_returns   — daily simple/log returns via REST
        get_volume          — daily share volume via REST (split-adjusted)
        get_daily_grouped   — all-tickers cross-section for one date
        get_previous_close  — previous trading day's bar per ticker
        get_snapshot        — current market snapshot
        get_last_price      — most-recent price per ticker (Series)
                              fallback: last_trade → day_close → prev_close
        get_last_volume     — most-recent trading volume per ticker (Series)
                              fallback: day_volume → prev_volume
        get_last_trade      — most recent trade per ticker
        get_last_quote      — most recent NBBO quote per ticker
        get_market_status   — open/closed status across exchanges
        get_market_holidays — upcoming market holidays

    Reference
        get_tickers         — list-endpoint universe (paginated)
        get_ticker_details  — rich per-ticker profile (~25 fields)
        get_ticker_types    — catalog of ticker type codes (CS/ETF/etc.)
        get_exchanges       — catalog of exchanges/venues
        get_related_tickers — similar tickers per query symbol

    Subpackages (call as equities.<sub>.<func>)
        cax           — corporate actions: get_dividends, get_splits
        fundamentals  — financial statements + ratios
        short_data    — short interest, short volume, floats
        etf           — ETF Global feed: constituents, fund_flows, etc.
        company       — classification: get_industry, get_sector
                        plus Benzinga stubs (entitlement required)

    Company classification
        get_industry      — SIC industry description per ticker
        get_sector        — broad SIC-division sector per ticker

    Company (Benzinga add-on — stubs require entitlement)
        get_major_news, get_earnings

    Corporate actions
        cax.get_dividends, cax.get_splits

Examples:
    >>> from agora import equities
    >>> prices  = equities.get_daily_prices(["AAPL", "MSFT"], period="1y")
    >>> returns = equities.get_daily_returns(["SPY"], start="2024-01-01")
    >>> snap    = equities.get_snapshot(["AAPL", "MSFT"])
    >>>
    >>> from agora.equities import cax
    >>> divs = cax.get_dividends("AAPL")
"""

# ── Subpackage exports ──────────────────────────────────────────────
from agora.equities import cax, company, etf, fundamentals, short_data

# ── Company ─────────────────────────────────────────────────────────
from agora.equities.company import (
    get_earnings,
    get_industry,
    get_major_news,
    get_sector,
)

# ── Market ──────────────────────────────────────────────────────────
from agora.equities.market import (
    get_daily_grouped,
    get_daily_prices,
    get_daily_returns,
    get_last_price,
    get_last_quote,
    get_last_trade,
    get_last_volume,
    get_market_holidays,
    get_market_status,
    get_previous_close,
    get_snapshot,
    get_volume,
)

# ── Reference ───────────────────────────────────────────────────────
from agora.equities.reference import (
    get_exchanges,
    get_related_tickers,
    get_ticker_details,
    get_ticker_types,
    get_tickers,
)

__all__ = [
    # Subpackages
    "cax",
    "company",
    "etf",
    "fundamentals",
    "short_data",
    # Market
    "get_daily_prices",
    "get_daily_returns",
    "get_volume",
    "get_daily_grouped",
    "get_previous_close",
    "get_snapshot",
    "get_last_price",
    "get_last_volume",
    "get_last_trade",
    "get_last_quote",
    "get_market_status",
    "get_market_holidays",
    # Reference
    "get_tickers",
    "get_ticker_details",
    "get_ticker_types",
    "get_exchanges",
    "get_related_tickers",
    # Company
    "get_industry",
    "get_sector",
    "get_major_news",
    "get_earnings",
]
