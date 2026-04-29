from __future__ import annotations

from typing import Any, Dict, Mapping

import pandas as pd


def normalize_grouped_daily_results(payload: Mapping[str, Any], date: str) -> pd.DataFrame:
    rows: list[Dict[str, Any]] = []
    for item in payload.get("results", []) or []:
        ts_ms = item.get("t")
        rows.append(
            {
                "date": date,
                "ticker": item.get("T") or item.get("ticker"),
                "open": item.get("o"),
                "high": item.get("h"),
                "low": item.get("l"),
                "close": item.get("c"),
                "volume": item.get("v"),
                "vwap": item.get("vw"),
                "trades": item.get("n"),
                "ts_ms": ts_ms,
                "ts_utc": pd.to_datetime(ts_ms, unit="ms", utc=True, errors="coerce"),
                "otc": item.get("otc"),
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "date",
                "ticker",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "vwap",
                "trades",
                "ts_ms",
                "ts_utc",
                "otc",
            ]
        )

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


def normalize_open_close(payload: Mapping[str, Any]) -> pd.DataFrame:
    if not payload:
        return pd.DataFrame(
            columns=[
                "date",
                "ticker",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "pre_market",
                "after_hours",
                "otc",
            ]
        )

    row = {
        "date": payload.get("from"),
        "ticker": payload.get("symbol"),
        "open": payload.get("open"),
        "high": payload.get("high"),
        "low": payload.get("low"),
        "close": payload.get("close"),
        "volume": payload.get("volume"),
        "pre_market": payload.get("preMarket"),
        "after_hours": payload.get("afterHours"),
        "otc": payload.get("otc"),
    }

    df = pd.DataFrame([row])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


_AGG_COLUMNS = [
    "date", "ticker", "open", "high", "low", "close", "volume", "vwap",
    "trades", "ts_ms", "ts_utc",
]


def normalize_aggregate_results(payload: Mapping[str, Any], ticker: str) -> pd.DataFrame:
    rows: list[Dict[str, Any]] = []
    for item in payload.get("results", []) or []:
        ts_ms = item.get("t")
        ts_utc = pd.to_datetime(ts_ms, unit="ms", utc=True, errors="coerce")
        rows.append(
            {
                "date": ts_utc.tz_convert(None).normalize() if pd.notna(ts_utc) else pd.NaT,
                "ticker": ticker,
                "open": item.get("o"),
                "high": item.get("h"),
                "low": item.get("l"),
                "close": item.get("c"),
                "volume": item.get("v"),
                "vwap": item.get("vw"),
                "trades": item.get("n"),
                "ts_ms": ts_ms,
                "ts_utc": ts_utc,
            }
        )

    if not rows:
        return pd.DataFrame(columns=_AGG_COLUMNS)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


def normalize_previous_day_results(payload: Mapping[str, Any], fallback_ticker: str) -> pd.DataFrame:
    rows: list[Dict[str, Any]] = []

    for item in payload.get("results", []) or []:
        ts_ms = item.get("t")
        ts_utc = pd.to_datetime(ts_ms, unit="ms", utc=True, errors="coerce")
        rows.append(
            {
                "date": ts_utc.tz_convert(None).normalize() if pd.notna(ts_utc) else pd.NaT,
                "ticker": payload.get("ticker") or item.get("T") or fallback_ticker,
                "open": item.get("o"),
                "high": item.get("h"),
                "low": item.get("l"),
                "close": item.get("c"),
                "volume": item.get("v"),
                "vwap": item.get("vw"),
                "trades": item.get("n"),
                "ts_ms": ts_ms,
                "ts_utc": ts_utc,
                "adjusted": payload.get("adjusted"),
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "date",
                "ticker",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "vwap",
                "trades",
                "ts_ms",
                "ts_utc",
                "adjusted",
            ]
        )

    return pd.DataFrame(rows)
