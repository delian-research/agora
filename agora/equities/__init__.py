"""Equity-domain API.

This is the user-facing layer for US equities. It speaks in terms of
"prices, returns, volume, snapshots, dividends, splits, classification" —
not "REST endpoints" or "Parquet paths."

Public surface (a "verb-shape" list):

    Market
        get_daily_prices  — pivoted OHLCV matrix over a date range
        get_daily_returns — daily simple/log returns
        get_volume        — daily share volume (split-adjusted)
        get_snapshot      — current market snapshot (live REST)

    Reference (stubs in v1)
        get_exchange, get_currency, get_country
        get_market_cap, get_shares_out

    Company (stubs; news/earnings need Benzinga entitlement)
        get_industry, get_sector
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

# ── Market ──────────────────────────────────────────────────────────
from agora.equities.market import (
    get_daily_prices,
    get_daily_returns,
    get_snapshot,
    get_volume,
)

# ── Reference (stubs) ───────────────────────────────────────────────
from agora.equities.reference import (
    get_country,
    get_currency,
    get_exchange,
    get_market_cap,
    get_shares_out,
)

# ── Company (stubs) ─────────────────────────────────────────────────
from agora.equities.company import (
    get_earnings,
    get_industry,
    get_major_news,
    get_sector,
)

__all__ = [
    # Subpackages
    "cax",
    "company",
    # Market
    "get_daily_prices",
    "get_daily_returns",
    "get_volume",
    "get_snapshot",
    # Reference
    "get_exchange",
    "get_currency",
    "get_country",
    "get_market_cap",
    "get_shares_out",
    # Company
    "get_industry",
    "get_sector",
    "get_major_news",
    "get_earnings",
]
