"""Dividend events from the local reference store.

Source: ``data/reference/dividends.parquet`` (populated by
``agora-download reference``).

The returned DataFrame columns mirror the Massive REST schema:
``ticker``, ``ex_dividend_date``, ``pay_date``, ``record_date``,
``declaration_date``, ``cash_amount``, ``currency``, ``frequency``,
``dividend_type``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd

from agora.loaders.parquet import FlatFileLoader


def get_dividends(
    tickers: str | Sequence[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    data_dir: Path | str | None = None,
) -> pd.DataFrame:
    """Dividend events filtered by ticker basket and ex-dividend date range.

    Args:
        tickers: One or more ticker symbols. ``None`` returns all.
        start: Earliest ex-dividend date (YYYY-MM-DD inclusive).
        end:   Latest ex-dividend date (YYYY-MM-DD inclusive).
        data_dir: Override the Parquet data directory.

    Returns:
        DataFrame sorted by ``(ex_dividend_date, ticker)`` with columns:
        ``ticker``, ``ex_dividend_date``, ``pay_date``, ``record_date``,
        ``declaration_date``, ``cash_amount``, ``currency``,
        ``frequency``, ``dividend_type``.

    Examples:
        >>> from agora.equities import cax
        >>> # All AAPL dividends
        >>> cax.get_dividends("AAPL")
        >>> # Q4 2024 dividends across a basket
        >>> cax.get_dividends(["AAPL", "MSFT"],
        ...                    start="2024-10-01", end="2024-12-31")
        >>> # All dividends declared in 2025
        >>> cax.get_dividends(start="2025-01-01", end="2025-12-31")
    """
    return FlatFileLoader(data_dir=data_dir).get_dividends(
        tickers, start=start, end=end
    )
