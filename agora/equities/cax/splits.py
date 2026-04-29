"""Stock split events from the local reference store.

Source: ``data/reference/splits.parquet`` (populated by
``agora-download reference``).

The returned DataFrame columns mirror the Massive REST schema:
``ticker``, ``execution_date``, ``split_from``, ``split_to``.

The split ratio is ``split_to / split_from`` (e.g., a 4-for-1 split is
``split_from=1, split_to=4``, ratio 4.0). Historical prices before the
split should be divided by the cumulative ratio of all subsequent splits;
historical volume should be multiplied by the same ratio. See
``agora.equities.market.get_daily_prices(adjusted=True)`` for the
canonical adjustment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd

from agora.loaders.parquet import FlatFileLoader


def get_splits(
    tickers: str | Sequence[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    data_dir: Path | str | None = None,
) -> pd.DataFrame:
    """Stock split events filtered by ticker basket and execution date range.

    Args:
        tickers: One or more ticker symbols. ``None`` returns all.
        start: Earliest execution date (YYYY-MM-DD inclusive).
        end:   Latest execution date (YYYY-MM-DD inclusive).
        data_dir: Override the Parquet data directory.

    Returns:
        DataFrame sorted by ``(execution_date, ticker)`` with columns:
        ``ticker``, ``execution_date``, ``split_from``, ``split_to``.

    Examples:
        >>> from agora.equities import cax
        >>> # All AAPL splits
        >>> cax.get_splits("AAPL")
        >>> # All splits in 2020 (e.g., AAPL 4:1, TSLA 5:1)
        >>> cax.get_splits(start="2020-01-01", end="2020-12-31")
    """
    return FlatFileLoader(data_dir=data_dir).get_splits(
        tickers, start=start, end=end
    )
