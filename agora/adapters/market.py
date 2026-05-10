"""Deprecated thin shim — use :mod:`agora.equities` instead.

This module exists only so older imports of
``agora.adapters.market.get_prices`` / ``get_returns`` keep working.
All real implementation now lives in :mod:`agora.equities.market` —
``get_daily_prices``, ``get_daily_returns``, ``get_volume``,
``get_snapshot`` — with a richer API (Parquet-or-REST source toggle,
``fields=``, structured ``failed_tickers``, etc.).

Migration::

    # Old (still works, warns)
    from agora.adapters import get_prices, get_returns
    prices  = get_prices(["AAPL"], period="1y")
    returns = get_returns(["AAPL"], period="1y")

    # New (preferred)
    from agora import equities
    prices  = equities.get_daily_prices(["AAPL"], period="1y", source="rest")
    returns = equities.get_daily_returns(["AAPL"], period="1y", source="rest")

Kwarg translation handled by this shim:

- ``adjust``  → ``adjusted``
- ``ohlcv=True`` → ``fields=("open", "high", "low", "close", "volume")``

The shim always uses ``source="rest"`` because that is what the original
adapter implementation did. If you want the local Parquet fast path,
call :func:`agora.equities.get_daily_prices` directly.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from typing import Literal

import pandas as pd

from agora.client import MassiveClient
from agora.equities.market import (
    Calendar,
    ReturnMethod,
    get_daily_prices,
    get_daily_returns,
)

__all__ = ["get_prices", "get_returns"]


def _warn_once(callsite: str) -> None:
    warnings.warn(
        f"agora.adapters.market.{callsite} is deprecated; use "
        f"agora.equities.{callsite.replace('get_', 'get_daily_')} instead. "
        "See agora/adapters/market.py for the migration guide.",
        DeprecationWarning,
        stacklevel=3,
    )


def get_prices(
    tickers: Iterable[str],
    *,
    start: str | None = None,
    end: str | None = None,
    period: str | None = None,
    adjust: bool = True,
    fill: bool = False,
    ohlcv: bool = False,
    calendar: Calendar = "union",
    strict: bool = False,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Deprecated. Use :func:`agora.equities.get_daily_prices` instead.

    Translates ``adjust`` → ``adjusted`` and ``ohlcv=True`` → multi-field
    ``fields=("open", "high", "low", "close", "volume")``, and forwards
    to the equities API with ``source="rest"`` (preserving the original
    adapter's REST-only behavior).
    """
    _warn_once("get_prices")
    fields: str | tuple[str, ...] = (
        ("open", "high", "low", "close", "volume") if ohlcv else "close"
    )
    return get_daily_prices(
        tickers,
        start=start,
        end=end,
        period=period,
        fields=fields,
        adjusted=adjust,
        source="rest",
        fill=fill,
        calendar=calendar,
        strict=strict,
        client=client,
    )


def get_returns(
    tickers: Iterable[str],
    *,
    start: str | None = None,
    end: str | None = None,
    period: str | None = None,
    method: ReturnMethod = "simple",
    adjust: bool = True,
    fill: bool = True,
    calendar: Calendar = "union",
    strict: bool = False,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Deprecated. Use :func:`agora.equities.get_daily_returns` instead."""
    _warn_once("get_returns")
    return get_daily_returns(
        tickers,
        start=start,
        end=end,
        period=period,
        method=method,
        adjusted=adjust,
        source="rest",
        fill=fill,
        calendar=calendar,
        strict=strict,
        client=client,
    )


# Re-export deprecated type aliases so old imports keep working.
CalendarMode = Literal["union", "intersection"]
