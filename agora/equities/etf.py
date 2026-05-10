"""ETF-specific data via Polygon's ETF Global feed.

Five helpers:

    - :func:`get_constituents`  ETF holdings (constituents and weights)
    - :func:`get_fund_flows`    daily net fund flows
    - :func:`get_profiles`      ETF profile metadata (issuer, AUM, fees, exposures)
    - :func:`get_analytics`     quant scores (risk, reward, technical, fundamental, etc.)
    - :func:`get_taxonomies`    classification (asset_class, category, focus, etc.)

All five share the same query pattern: filter by ``composite_ticker``
(the ETF symbol) and/or ``effective_date`` range.

Usage::

    >>> from agora.equities import etf
    >>> # SPY's holdings
    >>> etf.get_constituents("SPY")
    >>> # SPY's daily flows over the last year
    >>> etf.get_fund_flows("SPY", start="2024-01-01")
    >>> # SPY's profile (issuer, fees, exposure)
    >>> etf.get_profiles("SPY")
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


_DATE_COLUMNS = ("effective_date", "processed_date", "inception_date")


def get_constituents(
    composite_ticker: str | None = None,
    *,
    constituent_ticker: str | None = None,
    effective_date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """ETF holdings (constituents).

    Args:
        composite_ticker: ETF ticker (e.g. ``"SPY"``). ``None`` returns
            all ETFs (heavy — combine with date filters).
        constituent_ticker: Filter to ETFs that hold a specific
            underlying (e.g. ``"AAPL"`` to find every ETF that holds AAPL).
        effective_date: Single-day point-in-time snapshot.
        start: Earliest ``effective_date`` (YYYY-MM-DD).
        end: Latest ``effective_date`` (YYYY-MM-DD).
        limit: Per-page limit.
        client: Override the live REST client.

    Returns:
        DataFrame with columns: ``composite_ticker``,
        ``constituent_ticker``, ``constituent_name``, ``weight``,
        ``shares_held``, ``market_value``, ``asset_class``,
        ``security_type``, ``exchange``, ``country_of_exchange``,
        ``currency_traded``, identifiers (``isin``, ``sedol``, ``figi``,
        ``us_code``), ``effective_date``, ``processed_date``.

    Examples:
        >>> from agora.equities import etf
        >>> # SPY's full holdings on the latest date
        >>> etf.get_constituents("SPY")
        >>> # Every ETF that holds AAPL today
        >>> etf.get_constituents(constituent_ticker="AAPL",
        ...                      effective_date="2024-01-03")
    """
    c = client or get_client()
    records = c.rest.get_etf_global_constituents(
        composite_ticker=composite_ticker.strip().upper() if composite_ticker else None,
        constituent_ticker=constituent_ticker.strip().upper() if constituent_ticker else None,
        effective_date=effective_date,
        effective_date_gte=start,
        effective_date_lte=end,
        limit=limit,
    )
    return _records_to_dataframe(records, date_columns=_DATE_COLUMNS)


def get_fund_flows(
    composite_ticker: str | None = None,
    *,
    effective_date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Daily ETF fund flows (net creations / redemptions in USD).

    Args:
        composite_ticker: ETF ticker. ``None`` returns all ETFs.
        effective_date: Single-day snapshot.
        start / end: Range filter on ``effective_date``.
        limit: Per-page limit.
        client: Override the live REST client.

    Returns:
        DataFrame with columns: ``composite_ticker``, ``effective_date``,
        ``processed_date``, ``fund_flow`` (USD), ``nav``,
        ``shares_outstanding``.
    """
    c = client or get_client()
    records = c.rest.get_etf_global_fund_flows(
        composite_ticker=composite_ticker.strip().upper() if composite_ticker else None,
        effective_date=effective_date,
        effective_date_gte=start,
        effective_date_lte=end,
        limit=limit,
    )
    return _records_to_dataframe(records, date_columns=_DATE_COLUMNS)


def get_profiles(
    composite_ticker: str | None = None,
    *,
    effective_date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """ETF profile metadata.

    Returns ~50 columns describing each ETF: issuer, advisor, custodian,
    AUM, average daily volume, bid-ask spread, creation unit size,
    distribution frequency, exposure breakdowns (currency / industry /
    geographic / asset_class), inception date, etc.

    Args:
        composite_ticker: ETF ticker. ``None`` returns all ETFs.
        effective_date: Single-day snapshot.
        start / end: Range filter on ``effective_date``.
        limit: Per-page limit.
        client: Override the live REST client.
    """
    c = client or get_client()
    records = c.rest.get_etf_global_profiles(
        composite_ticker=composite_ticker.strip().upper() if composite_ticker else None,
        effective_date=effective_date,
        effective_date_gte=start,
        effective_date_lte=end,
        limit=limit,
    )
    return _records_to_dataframe(records, date_columns=_DATE_COLUMNS)


def get_analytics(
    composite_ticker: str | None = None,
    *,
    effective_date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """ETF quant analytics (risk, reward, technical, fundamental, sentiment).

    Returns ETF Global's quant scoring system: ``risk_total_score``,
    ``reward_score``, ``quant_total_score``, ``quant_grade``, plus
    sub-component scores across technical, sentiment, behavioral,
    fundamental, global, and quality dimensions.

    Args:
        composite_ticker: ETF ticker. ``None`` returns all ETFs.
        effective_date: Single-day snapshot.
        start / end: Range filter on ``effective_date``.
        limit: Per-page limit.
        client: Override the live REST client.
    """
    c = client or get_client()
    records = c.rest.get_etf_global_analytics(
        composite_ticker=composite_ticker.strip().upper() if composite_ticker else None,
        effective_date=effective_date,
        effective_date_gte=start,
        effective_date_lte=end,
        limit=limit,
    )
    return _records_to_dataframe(records, date_columns=_DATE_COLUMNS)


def get_taxonomies(
    composite_ticker: str | None = None,
    *,
    effective_date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """ETF taxonomy classifications.

    Returns ETF Global's classification system: ``asset_class``,
    ``category``, ``focus``, ``factor``, ``country``, ``duration``,
    ``credit_quality_rating``, ``leverage_style``, ``management_style``,
    ``rebalance_frequency``, ``primary_benchmark``, etc.

    Args:
        composite_ticker: ETF ticker. ``None`` returns all ETFs.
        effective_date: Single-day snapshot.
        start / end: Range filter on ``effective_date``.
        limit: Per-page limit.
        client: Override the live REST client.
    """
    c = client or get_client()
    records = c.rest.get_etf_global_taxonomies(
        composite_ticker=composite_ticker.strip().upper() if composite_ticker else None,
        effective_date=effective_date,
        effective_date_gte=start,
        effective_date_lte=end,
        limit=limit,
    )
    return _records_to_dataframe(records, date_columns=_DATE_COLUMNS)
