"""Equity-domain API.

This is the user-facing layer for US equities. It speaks in terms of
"prices, returns, volume, snapshots, dividends, splits, classification" —
not "REST endpoints" or "Parquet paths."

Public surface (a "verb-shape" list):

    Market
        get_daily_prices  — per-ticker OHLCV time-series via REST
        get_daily_returns — daily simple/log returns via REST
        get_volume        — daily share volume via REST (split-adjusted)
        get_daily_grouped — all-tickers cross-section for one date
        get_snapshot      — current market snapshot (live REST)

    Reference
        get_tickers         — list-endpoint universe (paginated)
        get_ticker_details  — rich per-ticker profile (~25 fields)
        get_ticker_types    — catalog of ticker type codes (CS/ETF/etc.)
        get_exchanges       — catalog of exchanges/venues
        get_related_tickers — similar tickers per query symbol

    Company classification
        get_industry      — SIC industry description per ticker
        get_sector        — broad SIC-division sector per ticker

    Company (Benzinga add-on — stubs require entitlement)
        get_major_news, get_earnings

    Corporate actions (stubs in v1)
        cax.get_dividends, cax.get_splits

Examples:
    >>> from agora import equities
    >>> prices  = equities.get_daily_prices(["AAPL", "MSFT"], period="1y")
    >>> returns = equities.get_daily_returns(["SPY"], start="2024-01-01")
    >>> snap    = equities.get_snapshot(["AAPL", "MSFT"])
    >>>
    >>> from agora.equities import cax
    >>> divs = cax.get_dividends("AAPL")           # NotImplementedError in v1
"""

# ── Subpackage exports ──────────────────────────────────────────────
from agora.equities import cax, company

# ── Company (stubs) ─────────────────────────────────────────────────
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
    # Market
    "get_daily_prices",
    "get_daily_returns",
    "get_volume",
    "get_daily_grouped",
    "get_snapshot",
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
