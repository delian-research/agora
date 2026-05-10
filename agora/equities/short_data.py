"""Short interest, short volume, and float data via the Massive REST API.

Three helpers:

    - :func:`get_short_interest`  wraps ``/stocks/v1/short-interest``
      (bi-monthly settlement-date snapshots of short positions)
    - :func:`get_short_volume`    wraps ``/stocks/v1/short-volume``
      (daily short volume per venue + ratios)
    - :func:`get_floats`          wraps ``/stocks/v1/floats``
      (free-float share counts and percentages)

Usage::

    >>> from agora.equities import short_data
    >>> short_data.get_short_interest("AAPL")
    >>> short_data.get_short_volume("AAPL", start="2024-01-01")
    >>> short_data.get_floats("AAPL")
"""

from __future__ import annotations

import pandas as pd

from agora.client import MassiveClient, get_client


def _records_to_dataframe(
    records: list,
    *,
    date_columns: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Generic SDK-records → DataFrame flattener (every non-private attr)."""
    if not records:
        return pd.DataFrame()
    rows: list[dict] = []
    for record in records:
        row: dict = {}
        for name in dir(record):
            if name.startswith("_"):
                continue
            try:
                value = getattr(record, name)
            except Exception:  # noqa: BLE001
                continue
            if callable(value):
                continue
            row[name] = value
        rows.append(row)
    df = pd.DataFrame(rows)
    for col in date_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def get_short_interest(
    ticker: str | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Short-interest records (bi-monthly settlement dates).

    Args:
        ticker: A single ticker symbol. ``None`` returns all available
            records (combine with date filters).
        start: Earliest ``settlement_date`` (YYYY-MM-DD).
        end: Latest ``settlement_date`` (YYYY-MM-DD).
        limit: Per-page limit.
        client: Override the live REST client.

    Returns:
        DataFrame with columns: ``ticker``, ``settlement_date``,
        ``short_interest``, ``avg_daily_volume``, ``days_to_cover``.

    Examples:
        >>> from agora.equities import short_data
        >>> short_data.get_short_interest("AAPL", start="2024-01-01")
    """
    c = client or get_client()
    records = c.rest.list_short_interest(
        ticker=ticker.strip().upper() if ticker else None,
        settlement_date_gte=start,
        settlement_date_lte=end,
        limit=limit,
    )
    return _records_to_dataframe(records, date_columns=("settlement_date",))


def get_short_volume(
    ticker: str | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Daily short-volume records.

    Args:
        ticker: A single ticker symbol. ``None`` returns all available.
        start: Earliest ``date`` (YYYY-MM-DD).
        end: Latest ``date`` (YYYY-MM-DD).
        limit: Per-page limit.
        client: Override the live REST client.

    Returns:
        DataFrame with columns: ``ticker``, ``date``, ``short_volume``,
        ``total_volume``, ``short_volume_ratio``, plus per-venue
        breakouts (``adf_short_volume``, ``nyse_short_volume``,
        ``nasdaq_carteret_short_volume``, ``nasdaq_chicago_short_volume``,
        and corresponding ``_exempt`` columns), ``exempt_volume``,
        ``non_exempt_volume``.
    """
    c = client or get_client()
    records = c.rest.list_short_volume(
        ticker=ticker.strip().upper() if ticker else None,
        date_gte=start,
        date_lte=end,
        limit=limit,
    )
    return _records_to_dataframe(records, date_columns=("date",))


def get_floats(
    ticker: str | None = None,
    *,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Stock-float records (free-float share counts and percentages).

    Args:
        ticker: A single ticker symbol. ``None`` returns all available.
        limit: Per-page limit.
        client: Override the live REST client.

    Returns:
        DataFrame with columns: ``ticker``, ``effective_date``,
        ``free_float``, ``free_float_percent``.
    """
    c = client or get_client()
    records = c.rest.list_stocks_floats(
        ticker=ticker.strip().upper() if ticker else None,
        limit=limit,
    )
    return _records_to_dataframe(records, date_columns=("effective_date",))
