"""Equity market data — historical bars, returns, volume, and live snapshots.

Public functions:
    - :func:`get_daily_prices` — pivoted OHLCV matrix over a date range
    - :func:`get_daily_returns` — daily returns derived from prices
    - :func:`get_volume` — daily share volume (split-adjusted by default)
    - :func:`get_snapshot` — current market snapshot (live REST only)

The historical functions (`get_daily_prices`, `get_daily_returns`,
`get_volume`) read from the local Parquet store by default — no rate
limit, no API calls. Pass ``source="rest"`` to fetch live from the
Massive REST API instead (per-ticker; slower).

Future: ``get_adv()`` and ``get_volatility()`` are derived stats that
will be added here when the use case shows up. Both are computed from
``get_daily_prices`` output, not separate raw fields.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Literal, Sequence

import numpy as np
import pandas as pd

from agora.client import MassiveClient, get_client
from agora.errors import MassiveAPIError
from agora.loaders.parquet import FlatFileLoader

logger = logging.getLogger(__name__)


# ── Constants ───────────────────────────────────────────────────────

_ALLOWED_PERIODS = {
    "1d", "5d", "1mo", "3mo", "6mo",
    "1y", "2y", "5y", "10y", "ytd",
}

_PERIOD_DELTAS = {
    "1d": datetime.timedelta(days=1),
    "5d": datetime.timedelta(days=5),
    "1mo": datetime.timedelta(days=30),
    "3mo": datetime.timedelta(days=90),
    "6mo": datetime.timedelta(days=183),
    "1y": datetime.timedelta(days=365),
    "2y": datetime.timedelta(days=730),
    "5y": datetime.timedelta(days=1825),
    "10y": datetime.timedelta(days=3650),
}

_VALID_FIELDS = ("open", "high", "low", "close", "volume", "trades", "vwap")

ReturnMethod = Literal["simple", "log"]
Source = Literal["parquet", "rest"]


# ── Internal helpers ────────────────────────────────────────────────

def _resolve_dates(
    start: str | None,
    end: str | None,
    period: str | None,
) -> tuple[str, str]:
    """Resolve ``period`` OR ``(start, end)`` into ISO date strings."""
    if period and (start or end):
        raise ValueError("Use either period OR start/end, not both.")

    if period:
        if period not in _ALLOWED_PERIODS:
            raise ValueError(
                f"Invalid period {period!r}. Allowed: {sorted(_ALLOWED_PERIODS)}"
            )
        end_dt = datetime.datetime.now(datetime.timezone.utc)
        if period == "ytd":
            start_dt = datetime.datetime(end_dt.year, 1, 1, tzinfo=datetime.timezone.utc)
        else:
            start_dt = end_dt - _PERIOD_DELTAS[period]
        return start_dt.date().isoformat(), end_dt.date().isoformat()

    if not start or not end:
        raise ValueError("Provide either period or both start and end dates.")

    return start, end


def _norm_tickers(tickers: str | Sequence[str]) -> list[str]:
    if isinstance(tickers, str):
        tickers = [tickers]
    out = [t.strip().upper() for t in tickers if t and t.strip()]
    if not out:
        raise ValueError("tickers must not be empty")
    return out


def _norm_fields(
    fields: str | Sequence[str],
) -> tuple[list[str], bool]:
    """Returns (fields_list, is_single)."""
    if isinstance(fields, str):
        if fields not in _VALID_FIELDS:
            raise ValueError(f"Invalid field {fields!r}. Allowed: {_VALID_FIELDS}")
        return [fields], True
    fields_list = list(fields)
    if not fields_list:
        raise ValueError("fields must not be empty")
    invalid = set(fields_list) - set(_VALID_FIELDS)
    if invalid:
        raise ValueError(
            f"Invalid field(s) {sorted(invalid)}. Allowed: {_VALID_FIELDS}"
        )
    return fields_list, False


def _apply_split_adjustment(
    prices: pd.DataFrame,
    splits: pd.DataFrame,
) -> pd.DataFrame:
    """Apply cumulative split factor to OHLC + volume in a long-format frame.

    For each row, the **cumulative ratio** is the product of split-ratios
    for splits that occur **strictly after** the row's date — i.e., a
    historical price gets divided down to today's basis, and historical
    volume gets multiplied up by the same factor (so dollar volume is
    preserved across the split).
    """
    if prices.empty or splits.empty:
        return prices

    out = prices.copy()
    price_cols = [c for c in ("open", "high", "low", "close", "vwap") if c in out.columns]

    for ticker, ticker_splits in splits.groupby("ticker"):
        mask = out["ticker"] == ticker
        if not mask.any():
            continue
        dates = out.loc[mask, "date"]
        factors = pd.Series(1.0, index=dates.index)
        for _, split in ticker_splits.iterrows():
            ratio = float(split["split_to"]) / float(split["split_from"])
            split_date = pd.Timestamp(split["execution_date"])
            factors[dates < split_date] *= ratio

        if (factors == 1.0).all():
            continue
        for col in price_cols:
            out.loc[mask, col] = out.loc[mask, col] / factors
        if "volume" in out.columns:
            out.loc[mask, "volume"] = out.loc[mask, "volume"] * factors

    return out


# ── Source: parquet ─────────────────────────────────────────────────

def _fetch_parquet(
    tickers: list[str],
    start: str,
    end: str,
    adjusted: bool,
    data_dir: Path | str | None,
) -> pd.DataFrame:
    """Long-format OHLCV DataFrame from local Parquet."""
    loader = FlatFileLoader(data_dir=data_dir)
    df = loader.get_stock_daily(tickers, start=start, end=end)
    if df.empty:
        return df
    if adjusted:
        df = _apply_split_adjustment(df, loader.get_splits())
    return df


# ── Source: rest ────────────────────────────────────────────────────

def _fetch_rest(
    tickers: list[str],
    start: str,
    end: str,
    adjusted: bool,
    client: MassiveClient | None,
) -> pd.DataFrame:
    """Long-format OHLCV DataFrame from live REST per ticker."""
    c = client or get_client()
    rows = []
    for t in tickers:
        try:
            aggs = c.rest.get_aggregates(
                t, multiplier=1, timespan="day",
                from_date=start, to_date=end,
                adjusted=adjusted,
            )
        except MassiveAPIError as e:
            logger.warning("REST aggregates for %s: %s", t, e)
            continue
        for a in aggs:
            ts = pd.to_datetime(a.timestamp, unit="ms", utc=True).tz_convert(None).normalize()
            rows.append({
                "date": ts,
                "ticker": t,
                "open": a.open, "high": a.high, "low": a.low, "close": a.close,
                "volume": a.volume,
                "trades": getattr(a, "transactions", None),
                "vwap": getattr(a, "vwap", None),
            })
    return pd.DataFrame(rows)


# ── Pivot helpers ───────────────────────────────────────────────────

def _pivot_single(
    df: pd.DataFrame,
    field: str,
    tickers_order: list[str],
) -> pd.DataFrame:
    """Long-format → date-indexed matrix, columns are tickers."""
    pivot = df.pivot_table(index="date", columns="ticker", values=field)
    cols = [t for t in tickers_order if t in pivot.columns]
    return pivot[cols]


def _pivot_multi(
    df: pd.DataFrame,
    fields: list[str],
    tickers_order: list[str],
) -> pd.DataFrame:
    """Long-format → date-indexed MultiIndex matrix, columns are (field, ticker)."""
    pieces = {}
    for f in fields:
        if f not in df.columns:
            continue
        pieces[f] = df.pivot_table(index="date", columns="ticker", values=f)
    if not pieces:
        return pd.DataFrame()
    combined = pd.concat(pieces, axis=1)
    new_cols = [(f, t) for f in fields for t in tickers_order if (f, t) in combined.columns]
    return combined[new_cols]


# ── Snapshot helpers ────────────────────────────────────────────────

def _snapshots_to_dataframe(snaps: list) -> pd.DataFrame:
    """Flatten a list of ``TickerSnapshot`` objects to a row-per-ticker DataFrame."""
    rows = []
    for s in snaps:
        day = getattr(s, "day", None)
        prev_day = getattr(s, "prev_day", None)
        last_trade = getattr(s, "last_trade", None)
        last_quote = getattr(s, "last_quote", None)
        min_bar = getattr(s, "min", None)

        rows.append({
            "ticker": getattr(s, "ticker", None),
            "todays_change": getattr(s, "todays_change", None),
            "todays_change_pct": getattr(s, "todays_change_perc", None),
            "day_open": getattr(day, "open", None) if day else None,
            "day_high": getattr(day, "high", None) if day else None,
            "day_low": getattr(day, "low", None) if day else None,
            "day_close": getattr(day, "close", None) if day else None,
            "day_volume": getattr(day, "volume", None) if day else None,
            "day_vwap": getattr(day, "vwap", None) if day else None,
            "prev_close": getattr(prev_day, "close", None) if prev_day else None,
            "prev_volume": getattr(prev_day, "volume", None) if prev_day else None,
            "last_trade_price": getattr(last_trade, "price", None) if last_trade else None,
            "last_trade_size": getattr(last_trade, "size", None) if last_trade else None,
            "last_quote_bid": getattr(last_quote, "bid", None) if last_quote else None,
            "last_quote_ask": getattr(last_quote, "ask", None) if last_quote else None,
            "min_close": getattr(min_bar, "close", None) if min_bar else None,
            "min_volume": getattr(min_bar, "volume", None) if min_bar else None,
            "updated_ns": getattr(s, "updated", None),
        })
    df = pd.DataFrame(rows)
    if not df.empty and "updated_ns" in df.columns:
        df["updated_utc"] = pd.to_datetime(
            df["updated_ns"], unit="ns", utc=True, errors="coerce"
        )
    return df


# ── Public API ──────────────────────────────────────────────────────

def get_daily_prices(
    tickers: str | Sequence[str],
    start: str | None = None,
    end: str | None = None,
    *,
    period: str | None = None,
    fields: str | Sequence[str] = "close",
    adjusted: bool = True,
    source: Source = "parquet",
    fill: bool = False,
    data_dir: Path | str | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Daily OHLCV prices for a basket of equity tickers.

    Args:
        tickers: One or more ticker symbols.
        start: Start date (YYYY-MM-DD inclusive). Mutually exclusive with ``period``.
        end:   End date (YYYY-MM-DD inclusive). Mutually exclusive with ``period``.
        period: Convenience for relative ranges: ``"1d"``, ``"5d"``, ``"1mo"``,
            ``"3mo"``, ``"6mo"``, ``"1y"``, ``"2y"``, ``"5y"``, ``"10y"``, ``"ytd"``.
        fields: Which OHLCV column(s) to return. Single string returns a flat
            matrix; sequence returns a MultiIndex ``(field, ticker)``.
        adjusted: Apply split adjustment. For ``source="parquet"``, splits are
            applied locally from ``data/reference/splits.parquet``. For
            ``source="rest"``, the API returns adjusted prices directly.
        source: ``"parquet"`` (local, fast) or ``"rest"`` (live, per-ticker).
        fill: Forward-fill missing values across the index.
        data_dir: Override the Parquet data directory.
        client: Override the live REST client.

    Returns:
        DataFrame indexed by date.

        - Single-field call (``fields="close"``): columns are tickers in
          input order.
        - Multi-field call (``fields=("open","close","volume")``): columns
          are a MultiIndex of ``(field, ticker)``.

    Examples:
        >>> get_daily_prices(["AAPL","MSFT"], period="1y")
        # Returns close-price matrix
        >>>
        >>> get_daily_prices("AAPL", start="2024-01-01", end="2024-12-31",
        ...                   fields=("open","close","volume"))
        # Returns MultiIndex columns: (open,AAPL), (close,AAPL), (volume,AAPL)
    """
    tickers_list = _norm_tickers(tickers)
    fields_list, is_single = _norm_fields(fields)
    start_date, end_date = _resolve_dates(start, end, period)

    if source == "parquet":
        df = _fetch_parquet(tickers_list, start_date, end_date, adjusted, data_dir)
    elif source == "rest":
        df = _fetch_rest(tickers_list, start_date, end_date, adjusted, client)
    else:
        raise ValueError(f"source must be 'parquet' or 'rest', got {source!r}")

    if df.empty:
        return df

    if is_single:
        result = _pivot_single(df, fields_list[0], tickers_list)
    else:
        result = _pivot_multi(df, fields_list, tickers_list)

    if fill:
        result = result.ffill()

    return result


def get_daily_returns(
    tickers: str | Sequence[str],
    start: str | None = None,
    end: str | None = None,
    *,
    period: str | None = None,
    method: ReturnMethod = "simple",
    adjusted: bool = True,
    source: Source = "parquet",
    fill: bool = True,
    data_dir: Path | str | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Daily returns for a basket of equity tickers.

    Wraps :func:`get_daily_prices` with ``fields="close"`` and computes
    period-over-period returns.

    Args:
        method: ``"simple"`` for ``(p_t / p_{t-1}) - 1``, or ``"log"`` for
            ``ln(p_t / p_{t-1})``.

    Returns:
        DataFrame indexed by date with one column per ticker.
    """
    prices = get_daily_prices(
        tickers, start, end,
        period=period, fields="close",
        adjusted=adjusted, source=source, fill=fill,
        data_dir=data_dir, client=client,
    )
    if prices.empty:
        return prices

    if method == "simple":
        returns = prices.pct_change()
    elif method == "log":
        returns = np.log(prices / prices.shift(1))
    else:
        raise ValueError(f"method must be 'simple' or 'log', got {method!r}")

    return returns.dropna(how="all")


def get_volume(
    tickers: str | Sequence[str],
    start: str | None = None,
    end: str | None = None,
    *,
    period: str | None = None,
    adjusted: bool = True,
    source: Source = "parquet",
    fill: bool = False,
    data_dir: Path | str | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Daily share volume for a basket of equity tickers.

    Wraps :func:`get_daily_prices` with ``fields="volume"``. With
    ``adjusted=True`` (default) historical volume is scaled up by the
    cumulative split ratio so dollar volume is preserved across splits.

    Returns:
        DataFrame indexed by date with one column per ticker.
    """
    return get_daily_prices(
        tickers, start, end,
        period=period, fields="volume",
        adjusted=adjusted, source=source, fill=fill,
        data_dir=data_dir, client=client,
    )


def get_snapshot(
    tickers: str | Sequence[str] | None = None,
    *,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Current market snapshot for one, many, or all US equity tickers.

    No historical equivalent — this is a live "now" reading via REST.

    Strategy:

    - **One ticker**: single-ticker REST call (smallest payload).
    - **Multiple tickers**: bulk ``get_snapshot_all`` (one API call returning
      every US ticker), filtered locally — dramatically faster than N
      per-ticker calls.
    - **None**: bulk fetch, no filter (~10K rows).

    Returns:
        DataFrame with one row per ticker, columns include ticker,
        todays_change, day OHLCV, prev_close, last_trade_price,
        last_quote bid/ask, min OHLCV, and updated_utc timestamp.
    """
    c = client or get_client()

    # Normalize ticker input
    if tickers is None:
        tickers_list: list[str] | None = None
    elif isinstance(tickers, str):
        tickers_list = [tickers.strip().upper()]
    else:
        tickers_list = [t.strip().upper() for t in tickers if t and t.strip()]
        if not tickers_list:
            tickers_list = None

    # Single-ticker fast path
    if tickers_list and len(tickers_list) == 1:
        try:
            snap = c.rest.get_snapshot(tickers_list[0])
            snaps = [snap]
        except MassiveAPIError as e:
            logger.warning("snapshot for %s: %s", tickers_list[0], e)
            return pd.DataFrame()
    else:
        # Bulk fetch + optional local filter
        all_snaps = c.rest.get_all_snapshots()
        if tickers_list:
            ticker_set = set(tickers_list)
            snaps = [s for s in all_snaps if getattr(s, "ticker", None) in ticker_set]
        else:
            snaps = list(all_snaps)

    return _snapshots_to_dataframe(snaps)
