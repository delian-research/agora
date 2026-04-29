"""Stock splits from the local reference store.

Source: ``data/reference/splits.parquet`` (populated by
``agora-download reference``). Stub in v1.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd


def get_splits(
    tickers: str | Sequence[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Stock split events filtered by ticker basket and execution date range.

    Args:
        tickers: One or more ticker symbols. ``None`` returns all.
        start: Earliest execution date (YYYY-MM-DD inclusive).
        end:   Latest execution date (YYYY-MM-DD inclusive).

    Returns:
        DataFrame with columns: ``ticker``, ``execution_date``,
        ``split_from``, ``split_to``.
    """
    raise NotImplementedError(
        "agora.equities.cax.get_splits() is scaffolded but not yet "
        "implemented. Track progress in 2.Projects.md."
    )
