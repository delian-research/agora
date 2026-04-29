from datetime import datetime, timezone, timedelta
from typing import Iterable, Literal, Optional

import numpy as np
import pandas as pd

from agora.client import MassiveClient, get_client as _get_client
from agora.errors import MassiveAPIError


_client: MassiveClient|None = None

_ALLOWED_PERIODS = {
    "1d", "5d", "1mo", "3mo", "6mo",
    "1y", "2y", "5y", "10y", "ytd",
}

_PERIOD_DELTAS = {
    "1d": timedelta(days=1),
    "5d": timedelta(days=5),
    "1mo": timedelta(days=30),
    "3mo": timedelta(days=90),
    "6mo": timedelta(days=183),
    "1y": timedelta(days=365),
    "2y": timedelta(days=730),
    "5y": timedelta(days=1825),
    "10y": timedelta(days=3650),
}

ReturnMethod = Literal["simple", "log"]
CalendarMode = Literal["union", "intersection"]


def _resolve_dates(
    start: Optional[str],
    end: Optional[str],
    period: Optional[str],
) -> tuple[str, str]:
    if period and (start or end):
        raise ValueError("Use either period OR start/end, not both.")

    if period:
        if period not in _ALLOWED_PERIODS:
            raise ValueError(f"Invalid period '{period}'. Allowed: {sorted(_ALLOWED_PERIODS)}")
        end_dt = datetime.now(timezone.utc)
        if period == "ytd":
            start_dt = datetime(end_dt.year, 1, 1, tzinfo=timezone.utc)
        else:
            start_dt = end_dt - _PERIOD_DELTAS[period]
        return start_dt.date().isoformat(), end_dt.date().isoformat()

    if not start or not end:
        raise ValueError("Provide either period or both start and end dates.")

    return start, end


def _align_index(
    frames: dict[str, pd.DataFrame],
    calendar: CalendarMode,
) -> pd.DatetimeIndex:
    indices = [df.index for df in frames.values() if not df.empty]
    if not indices:
        return pd.DatetimeIndex([], name="date")

    idx = indices[0]
    for i in indices[1:]:
        idx = idx.union(i) if calendar == "union" else idx.intersection(i)
    return idx.sort_values()


def get_prices(
    tickers: Iterable[str],
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: Optional[str] = None,
    adjust: bool = True,
    fill: bool = False,
    ohlcv: bool = False,
    calendar: CalendarMode = "union",
    client: Optional[MassiveClient] = None,
) -> pd.DataFrame:
    """Fetch daily prices for one or more tickers.

    Returns a date-indexed DataFrame. By default returns Close prices
    with one column per ticker. Set ``ohlcv=True`` for MultiIndex
    columns ``(field, ticker)``.

    Examples::

        from agora.adapters.market import get_prices

        # Close prices for a basket, last year
        prices = get_prices(["AAPL", "MSFT", "GOOGL"], period="1y")

        # Full OHLCV bars for a date range
        bars = get_prices(["SPY"], start="2024-01-01", end="2024-12-31", ohlcv=True)
    """
    tickers_list = [t.strip().upper() for t in tickers if t and t.strip()]
    if not tickers_list:
        raise ValueError("tickers must not be empty")

    start_date, end_date = _resolve_dates(start, end, period)
    c = client or _get_client()

    per_ticker: dict[str, pd.DataFrame] = {}
    for ticker in tickers_list:
        try:
            aggs = c.rest.get_aggregates(
                ticker,
                multiplier=1,
                timespan="day",
                from_date=start_date,
                to_date=end_date,
                adjusted=adjust,
            )
        except MassiveAPIError:
            continue

        if not aggs:
            continue

        df = pd.DataFrame(
            [
                {
                    "date": pd.to_datetime(
                        a.timestamp, unit="ms", utc=True
                    ).tz_convert(None).normalize(),
                    "Open": a.open,
                    "High": a.high,
                    "Low": a.low,
                    "Close": a.close,
                    "Volume": a.volume,
                    "VWAP": getattr(a, "vwap", None),
                }
                for a in aggs
            ]
        ).set_index("date")
        df.index.name = "date"
        per_ticker[ticker] = df

    if not per_ticker:
        return pd.DataFrame()

    master_idx = _align_index(per_ticker, calendar)

    if ohlcv:
        combined = pd.concat(per_ticker, axis=1)
        combined.columns = combined.columns.swaplevel(0, 1)
        combined = combined.sort_index(axis=1)
        combined = combined.reindex(index=master_idx)
    else:
        combined = pd.DataFrame(
            {t: df["Close"] for t, df in per_ticker.items()}
        )
        combined = combined.reindex(columns=tickers_list)
        combined = combined.reindex(index=master_idx)

    combined = combined.dropna(how="all").sort_index()

    if fill:
        combined = combined.ffill()

    return combined


def get_returns(
    tickers: Iterable[str],
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: Optional[str] = None,
    method: ReturnMethod = "simple",
    adjust: bool = True,
    fill: bool = True,
    client: Optional[MassiveClient] = None,
) -> pd.DataFrame:
    """Compute daily returns for one or more tickers.

    Returns a date-indexed DataFrame with one column per ticker.

    Examples::

        from agora.adapters.market import get_returns

        returns = get_returns(["AAPL", "MSFT"], period="1y")
        log_returns = get_returns(["AAPL"], period="2y", method="log")
    """
    prices = get_prices(
        tickers,
        start=start,
        end=end,
        period=period,
        adjust=adjust,
        fill=fill,
        client=client,
    )

    if prices.empty:
        return pd.DataFrame()

    if method == "simple":
        returns = prices.pct_change()
    elif method == "log":
        returns = np.log(prices / prices.shift(1))
    else:
        raise ValueError("method must be 'simple' or 'log'")

    return returns.dropna(how="all")
