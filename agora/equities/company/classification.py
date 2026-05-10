"""Industry / sector classification derived from SIC codes.

Both functions are thin wrappers over :func:`agora.equities.get_ticker_details`
— the underlying API call is the same. Use ``get_ticker_details`` directly
if you need the raw ``sic_code`` / ``sic_description`` plus other fields
in a single call.

SIC (Standard Industrial Classification) codes are 4 digits. The first
two digits map to a major group (~80 categories); ranges of major
groups roll up to broader divisions (10 categories). We expose:

    - :func:`get_industry`  → ``sic_description`` (free-text, ~80 buckets)
    - :func:`get_sector`    → SIC division (10 broad sectors)

For finer-grained classification (GICS, ICB, etc.) you'll need a vendor
beyond Polygon. SIC is the standard Polygon ships.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from agora.client import MassiveClient
from agora.equities.reference import get_ticker_details

# ── SIC code → broad sector (division) mapping ─────────────────────
#
# Source: U.S. Department of Labor SIC division structure.
# Each tuple is (lower_inclusive, upper_inclusive, sector_label).

_SIC_DIVISIONS: tuple[tuple[int, int, str], ...] = (
    (100,   999,  "Agriculture, Forestry, Fishing"),
    (1000,  1499, "Mining"),
    (1500,  1799, "Construction"),
    (2000,  3999, "Manufacturing"),
    (4000,  4999, "Transportation, Communications, Utilities"),
    (5000,  5199, "Wholesale Trade"),
    (5200,  5999, "Retail Trade"),
    (6000,  6799, "Finance, Insurance, Real Estate"),
    (7000,  8999, "Services"),
    (9100,  9899, "Public Administration"),
    (9900,  9999, "Nonclassifiable"),
)


def _sic_to_sector(sic_code) -> str | None:
    """Map a SIC code (string or int) to its broad-sector division name."""
    if sic_code is None or (isinstance(sic_code, float) and pd.isna(sic_code)):
        return None
    try:
        code = int(str(sic_code).strip())
    except (ValueError, TypeError):
        return None
    for low, high, label in _SIC_DIVISIONS:
        if low <= code <= high:
            return label
    return None


# ── Public API ──────────────────────────────────────────────────────


def get_industry(
    tickers: str | Sequence[str],
    *,
    client: MassiveClient | None = None,
) -> pd.Series:
    """SIC industry description per ticker.

    Returns the ``sic_description`` field from Polygon's ticker-details
    endpoint as a Series indexed by ticker.

    Args:
        tickers: One or more ticker symbols.
        client: Override the live REST client.

    Returns:
        Series indexed by ticker with the SIC industry description as
        the value. Tickers with no SIC code map to ``None``.

    Examples:
        >>> from agora import equities
        >>> equities.get_industry(["AAPL", "JPM", "XOM"])
        AAPL                       Electronic Computers
        JPM     National Commercial Banks
        XOM     Petroleum Refining
        Name: industry, dtype: object
    """
    df = get_ticker_details(tickers, client=client)
    if df.empty:
        return pd.Series(dtype="object", name="industry")
    return df.set_index("ticker")["sic_description"].rename("industry")


def get_sector(
    tickers: str | Sequence[str],
    *,
    client: MassiveClient | None = None,
) -> pd.Series:
    """Broad sector classification per ticker (SIC division).

    Maps each ticker's ``sic_code`` to one of the 10 SIC divisions
    (Manufacturing, Finance/Insurance/Real Estate, Services, etc.).
    For finer granularity use :func:`get_industry`.

    Args:
        tickers: One or more ticker symbols.
        client: Override the live REST client.

    Returns:
        Series indexed by ticker with the sector label as the value.
        Tickers with an unmapped or missing SIC code yield ``None``.

    Examples:
        >>> from agora import equities
        >>> equities.get_sector(["AAPL", "JPM", "XOM"])
        AAPL                          Manufacturing
        JPM     Finance, Insurance, Real Estate
        XOM                          Manufacturing
        Name: sector, dtype: object
    """
    df = get_ticker_details(tickers, client=client)
    if df.empty:
        return pd.Series(dtype="object", name="sector")
    sectors = df["sic_code"].map(_sic_to_sector)
    sectors.index = df["ticker"]
    sectors.name = "sector"
    return sectors
