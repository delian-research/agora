"""Financial statements and ratios via the Massive REST API.

Four helpers, one per Polygon financials endpoint:

    - :func:`get_balance_sheets`        wraps ``/v3/reference/financials/balance-sheets``
    - :func:`get_cash_flow_statements`  wraps ``/v3/reference/financials/cash-flow-statements``
    - :func:`get_income_statements`     wraps ``/v3/reference/financials/income-statements``
    - :func:`get_ratios`                wraps ``/v3/reference/financials/ratios``

The first three return one row per (ticker, period_end, timeframe).
:func:`get_ratios` returns daily point-in-time market-derived ratios
(P/E, P/B, dividend_yield, debt_to_equity, ev_to_ebitda, etc.).

Each helper auto-paginates via the SDK and returns every line item the
API surfaces — column count is high (~30-40 per statement). Use
``df[[col1, col2, ...]]`` to project the fields you actually need.

Usage::

    >>> from agora.equities import fundamentals
    >>> fundamentals.get_income_statements("AAPL", timeframe="annual")
    >>> fundamentals.get_balance_sheets(
    ...     "AAPL", period_end_gte="2020-01-01", timeframe="quarterly",
    ... )
    >>> fundamentals.get_ratios("AAPL")
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from agora.client import MassiveClient, get_client

# ── Common helpers ──────────────────────────────────────────────────


def _norm_basket(tickers: str | Sequence[str] | None) -> str | None:
    """Tickers → comma-separated string the SDK expects, or None for all."""
    if tickers is None:
        return None
    if isinstance(tickers, str):
        tickers = [tickers]
    out = [t.strip().upper() for t in tickers if t and t.strip()]
    return ",".join(out) if out else None


def _records_to_dataframe(
    records: list,
    *,
    date_columns: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Generic SDK-records → DataFrame flattener.

    Extracts every non-callable, non-private attribute from each record.
    Casts the named ``date_columns`` to datetime.
    """
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


# ── Public API ──────────────────────────────────────────────────────


def get_balance_sheets(
    tickers: str | Sequence[str] | None = None,
    *,
    cik: str | None = None,
    start: str | None = None,
    end: str | None = None,
    timeframe: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Balance-sheet records.

    Args:
        tickers: One or more ticker symbols. ``None`` returns all
            available (heavy — usually combine with date / cik filters).
        cik: SEC CIK (alternative identifier; pass instead of ``tickers``).
        start: Earliest ``period_end`` (YYYY-MM-DD).
        end: Latest ``period_end`` (YYYY-MM-DD).
        timeframe: ``"annual"`` / ``"quarterly"``. ``None`` returns both.
        limit: Per-page limit (SDK paginates beyond).
        client: Override the live REST client.

    Returns:
        DataFrame with one row per (ticker, period_end, timeframe) and
        ~30 balance-sheet line items as columns (assets, liabilities,
        equity components). Date columns ``period_end`` and
        ``filing_date`` are coerced to ``datetime64``.
    """
    c = client or get_client()
    records = c.rest.list_financials_balance_sheets(
        tickers=_norm_basket(tickers),
        cik=cik,
        period_end_gte=start,
        period_end_lte=end,
        timeframe=timeframe,
        limit=limit,
    )
    return _records_to_dataframe(
        records, date_columns=("period_end", "filing_date"),
    )


def get_cash_flow_statements(
    tickers: str | Sequence[str] | None = None,
    *,
    cik: str | None = None,
    start: str | None = None,
    end: str | None = None,
    timeframe: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Cash-flow statements.

    Returns DataFrame with one row per (ticker, period_end, timeframe)
    and ~25 cash-flow line items (operating / investing / financing).
    See :func:`get_balance_sheets` for argument semantics.
    """
    c = client or get_client()
    records = c.rest.list_financials_cash_flow_statements(
        tickers=_norm_basket(tickers),
        cik=cik,
        period_end_gte=start,
        period_end_lte=end,
        timeframe=timeframe,
        limit=limit,
    )
    return _records_to_dataframe(
        records, date_columns=("period_end", "filing_date"),
    )


def get_income_statements(
    tickers: str | Sequence[str] | None = None,
    *,
    cik: str | None = None,
    start: str | None = None,
    end: str | None = None,
    timeframe: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Income statements.

    Returns DataFrame with one row per (ticker, period_end, timeframe)
    and ~25 income-statement line items (revenue, costs, EPS, EBITDA,
    interest, taxes). See :func:`get_balance_sheets` for argument
    semantics.
    """
    c = client or get_client()
    records = c.rest.list_financials_income_statements(
        tickers=_norm_basket(tickers),
        cik=cik,
        period_end_gte=start,
        period_end_lte=end,
        timeframe=timeframe,
        limit=limit,
    )
    return _records_to_dataframe(
        records, date_columns=("period_end", "filing_date"),
    )


def get_ratios(
    ticker: str | None = None,
    *,
    cik: str | None = None,
    limit: int | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Daily point-in-time financial ratios.

    Returns DataFrame with one row per (ticker, date) and ~22 ratio
    columns including:

    - Valuation: ``price_to_earnings``, ``price_to_book``,
      ``price_to_sales``, ``price_to_cash_flow``,
      ``price_to_free_cash_flow``, ``ev_to_sales``, ``ev_to_ebitda``,
      ``enterprise_value``, ``market_cap``.
    - Returns / coverage: ``return_on_assets``, ``return_on_equity``,
      ``debt_to_equity``, ``current``, ``quick``, ``cash``.
    - Other: ``dividend_yield``, ``earnings_per_share``,
      ``free_cash_flow``, ``price``, ``average_volume``.

    Unlike the statement endpoints, this is per-ticker (not basket).
    Loop client-side for a basket.

    Args:
        ticker: A single ticker symbol.
        cik: SEC CIK (alternative identifier).
        limit: Per-page limit.
        client: Override the live REST client.
    """
    c = client or get_client()
    records = c.rest.list_financials_ratios(
        ticker=ticker.strip().upper() if ticker else None,
        cik=cik,
        limit=limit,
    )
    return _records_to_dataframe(records, date_columns=("date",))
