"""Dividend events via the Massive REST API.

The returned DataFrame columns mirror the existing schema so downstream
callers don't need to change:

    ``ticker``, ``ex_dividend_date``, ``pay_date``, ``record_date``,
    ``declaration_date``, ``cash_amount``, ``currency``, ``frequency``,
    ``dividend_type``.

This is the API client. Downstream packages that want a local cache
should layer their own caching on top — `agora` deliberately does not
read from `data/reference/dividends.parquet` here.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from agora.client import MassiveClient, get_client

_DATE_COLUMNS = ("ex_dividend_date", "pay_date", "record_date", "declaration_date")
_OUTPUT_COLUMNS = (
    "ticker",
    "ex_dividend_date",
    "pay_date",
    "record_date",
    "declaration_date",
    "cash_amount",
    "currency",
    "frequency",
    "dividend_type",
)


def _norm_ticker_basket(
    tickers: str | Sequence[str] | None,
) -> list[str] | None:
    if tickers is None:
        return None
    if isinstance(tickers, str):
        tickers = [tickers]
    out = [t.strip().upper() for t in tickers if t and t.strip()]
    return out or None


def _records_to_dataframe(records: list) -> pd.DataFrame:
    """Flatten SDK dividend objects into the canonical DataFrame shape."""
    if not records:
        return pd.DataFrame(columns=list(_OUTPUT_COLUMNS))
    rows = [
        {
            "ticker": getattr(r, "ticker", None),
            "ex_dividend_date": getattr(r, "ex_dividend_date", None),
            "pay_date": getattr(r, "pay_date", None),
            "record_date": getattr(r, "record_date", None),
            "declaration_date": getattr(r, "declaration_date", None),
            "cash_amount": getattr(r, "cash_amount", None),
            "currency": getattr(r, "currency", None),
            "frequency": getattr(r, "frequency", None),
            "dividend_type": getattr(r, "dividend_type", None),
        }
        for r in records
    ]
    df = pd.DataFrame(rows, columns=list(_OUTPUT_COLUMNS))
    for col in _DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def get_dividends(
    tickers: str | Sequence[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Dividend events filtered by ticker basket and ex-dividend date range.

    Per-ticker call when ``tickers`` is provided; single bulk call when
    ``tickers`` is ``None``. Filters on ex-dividend date.

    Args:
        tickers: One or more ticker symbols. ``None`` returns all
            dividends for the date range (a wide bulk pull).
        start: Earliest ex-dividend date (YYYY-MM-DD inclusive).
        end:   Latest ex-dividend date (YYYY-MM-DD inclusive).
        client: Override the live REST client.

    Returns:
        DataFrame sorted by ``(ex_dividend_date, ticker)`` with columns:
        ``ticker``, ``ex_dividend_date``, ``pay_date``, ``record_date``,
        ``declaration_date``, ``cash_amount``, ``currency``,
        ``frequency``, ``dividend_type``.

    Examples:
        >>> from agora.equities import cax
        >>> cax.get_dividends("AAPL")
        >>> cax.get_dividends(["AAPL", "MSFT"],
        ...                    start="2024-10-01", end="2024-12-31")
        >>> cax.get_dividends(start="2025-01-01", end="2025-12-31")
    """
    c = client or get_client()
    basket = _norm_ticker_basket(tickers)

    if basket is None:
        records = c.rest.list_dividends(
            ex_dividend_date_gte=start,
            ex_dividend_date_lte=end,
        )
    else:
        records = []
        for t in basket:
            records.extend(c.rest.list_dividends(
                ticker=t,
                ex_dividend_date_gte=start,
                ex_dividend_date_lte=end,
            ))

    df = _records_to_dataframe(records)
    if df.empty:
        return df
    return df.sort_values(["ex_dividend_date", "ticker"]).reset_index(drop=True)
