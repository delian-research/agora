from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

from agora.normalize.base import normalize_records


def normalize_snapshot_records(records: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for item in records:
        day = item.get("day") or {}
        min_bar = item.get("min") or {}
        prev_day = item.get("prevDay") or {}
        last_trade = item.get("lastTrade") or {}
        last_quote = item.get("lastQuote") or {}

        rows.append(
            {
                "ticker": item.get("ticker"),
                "updated_ns": item.get("updated"),
                "todays_change": item.get("todaysChange"),
                "todays_change_pct": item.get("todaysChangePerc"),
                "day_open": day.get("o"),
                "day_high": day.get("h"),
                "day_low": day.get("l"),
                "day_close": day.get("c"),
                "day_volume": day.get("v"),
                "day_vwap": day.get("vw"),
                "min_open": min_bar.get("o"),
                "min_high": min_bar.get("h"),
                "min_low": min_bar.get("l"),
                "min_close": min_bar.get("c"),
                "min_volume": min_bar.get("v"),
                "min_vwap": min_bar.get("vw"),
                "min_ts_ms": min_bar.get("t"),
                "prev_day_open": prev_day.get("o"),
                "prev_day_high": prev_day.get("h"),
                "prev_day_low": prev_day.get("l"),
                "prev_day_close": prev_day.get("c"),
                "prev_day_volume": prev_day.get("v"),
                "prev_day_vwap": prev_day.get("vw"),
                "last_trade_price": last_trade.get("p"),
                "last_trade_size": last_trade.get("s"),
                "last_trade_ns": last_trade.get("t"),
                "last_quote_bid": last_quote.get("p") if "p" in last_quote else last_quote.get("b"),
                "last_quote_ask": last_quote.get("P") if "P" in last_quote else last_quote.get("a"),
                "last_quote_ns": last_quote.get("t"),
            }
        )

    if not rows:
        return pd.DataFrame()

    return normalize_records(rows)


def normalize_single_snapshot_payload(payload: Mapping[str, Any]) -> pd.DataFrame:
    ticker_payload = payload.get("ticker")
    if not isinstance(ticker_payload, Mapping):
        return pd.DataFrame()
    return normalize_snapshot_records([ticker_payload])


def normalize_full_snapshot_payload(payload: Mapping[str, Any]) -> pd.DataFrame:
    tickers = payload.get("tickers", []) or []
    return normalize_snapshot_records(tickers)
