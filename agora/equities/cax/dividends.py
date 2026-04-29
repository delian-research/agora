"""Dividend events from the local reference store.

Source: ``data/reference/dividends.parquet`` (populated by
``agora-download reference``). Stub in v1.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd


def get_dividends(
    tickers: str | Sequence[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Dividend events filtered by ticker basket and ex-dividend date range.

    Args:
        tickers: One or more ticker symbols. ``None`` returns all.
        start: Earliest ex-dividend date (YYYY-MM-DD inclusive).
        end:   Latest ex-dividend date (YYYY-MM-DD inclusive).

    Returns:
        DataFrame with columns: ``ticker``, ``ex_dividend_date``,
        ``pay_date``, ``record_date``, ``declaration_date``,
        ``cash_amount``, ``currency``, ``frequency``, ``dividend_type``.
    """
    raise NotImplementedError(
        "agora.equities.cax.get_dividends() is scaffolded but not yet "
        "implemented. Track progress in 2.Projects.md."
    )
