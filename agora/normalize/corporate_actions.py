from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from agora.normalize.base import normalize_records

_DIVIDEND_DATE_COLUMNS = [
    "declaration_date",
    "ex_dividend_date",
    "record_date",
    "pay_date",
]


def normalize_dividends(payload: Mapping[str, Any]) -> pd.DataFrame:
    results = payload.get("results", []) or []
    if not results:
        return pd.DataFrame()

    df = normalize_records(results)
    for col in _DIVIDEND_DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def normalize_splits(payload: Mapping[str, Any]) -> pd.DataFrame:
    results = payload.get("results", []) or []
    if not results:
        return pd.DataFrame()

    df = normalize_records(results)
    if "execution_date" in df.columns:
        df["execution_date"] = pd.to_datetime(df["execution_date"], errors="coerce")
    return df
