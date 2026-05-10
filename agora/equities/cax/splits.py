"""Stock split events via the Massive REST API.

The returned DataFrame columns mirror the existing schema so downstream
callers don't need to change:

    ``ticker``, ``execution_date``, ``split_from``, ``split_to``.

The split ratio is ``split_to / split_from`` (e.g., a 4-for-1 split is
``split_from=1, split_to=4``, ratio 4.0). Historical prices before the
split should be divided by the cumulative ratio of all subsequent splits;
historical volume should be multiplied by the same ratio. See
``agora.equities.market._apply_split_adjustment`` for the helper that
applies this client-side when ``adjusted=False`` is passed to
``get_daily_prices``.

This is the API client. Downstream packages that want a local cache
should layer their own caching on top — `agora` deliberately does not
read from `data/reference/splits.parquet` here.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from agora.client import MassiveClient, get_client

_OUTPUT_COLUMNS = ("ticker", "execution_date", "split_from", "split_to")


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
    """Flatten SDK split objects into the canonical DataFrame shape."""
    if not records:
        return pd.DataFrame(columns=list(_OUTPUT_COLUMNS))
    rows = [
        {
            "ticker": getattr(r, "ticker", None),
            "execution_date": getattr(r, "execution_date", None),
            "split_from": getattr(r, "split_from", None),
            "split_to": getattr(r, "split_to", None),
        }
        for r in records
    ]
    df = pd.DataFrame(rows, columns=list(_OUTPUT_COLUMNS))
    df["execution_date"] = pd.to_datetime(df["execution_date"], errors="coerce")
    return df


def get_splits(
    tickers: str | Sequence[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Stock split events filtered by ticker basket and execution date range.

    Per-ticker call when ``tickers`` is provided; single bulk call when
    ``tickers`` is ``None``. Filters on execution date.

    Args:
        tickers: One or more ticker symbols. ``None`` returns all
            splits for the date range.
        start: Earliest execution date (YYYY-MM-DD inclusive).
        end:   Latest execution date (YYYY-MM-DD inclusive).
        client: Override the live REST client.

    Returns:
        DataFrame sorted by ``(execution_date, ticker)`` with columns:
        ``ticker``, ``execution_date``, ``split_from``, ``split_to``.

    Examples:
        >>> from agora.equities import cax
        >>> cax.get_splits("AAPL")
        >>> cax.get_splits(start="2020-01-01", end="2020-12-31")
    """
    c = client or get_client()
    basket = _norm_ticker_basket(tickers)

    if basket is None:
        records = c.rest.list_splits(
            execution_date_gte=start,
            execution_date_lte=end,
        )
    else:
        records = []
        for t in basket:
            records.extend(c.rest.list_splits(
                ticker=t,
                execution_date_gte=start,
                execution_date_lte=end,
            ))

    df = _records_to_dataframe(records)
    if df.empty:
        return df
    return df.sort_values(["execution_date", "ticker"]).reset_index(drop=True)
