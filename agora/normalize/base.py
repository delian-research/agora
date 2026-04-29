from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Mapping, Optional

import pandas as pd


_MS_MIN = 1.0e11
_MS_MAX = 1.0e13
_NS_MIN = 1.0e17
_NS_MAX = 9.22e18


def to_snake_case(name: str) -> str:
    converted = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    converted = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", converted)
    converted = re.sub(r"[^0-9a-zA-Z_]+", "_", converted)
    converted = re.sub(r"_+", "_", converted).strip("_")
    return converted.lower()


def flatten_record(record: Mapping[str, Any], *, prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for key, value in record.items():
        snake_key = to_snake_case(str(key))
        flat_key = f"{prefix}_{snake_key}" if prefix else snake_key

        if isinstance(value, Mapping):
            flat.update(flatten_record(value, prefix=flat_key))
        else:
            flat[flat_key] = value
    return flat


def _infer_epoch_unit(col_name: str, series: pd.Series) -> Optional[str]:
    if col_name.endswith("_ms"):
        return "ms"
    if col_name.endswith("_ns"):
        return "ns"

    numeric = pd.to_numeric(series, errors="coerce").dropna().astype("float64")
    if numeric.empty:
        return None

    if bool(((numeric >= _NS_MIN) & (numeric < _NS_MAX)).all()):
        return "ns"
    if bool(((numeric >= _MS_MIN) & (numeric < _MS_MAX)).all()):
        return "ms"
    return None


def _normalize_epoch_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    renamed = df.copy()
    rename_map: Dict[str, str] = {}
    epoch_cols: list[tuple[str, str]] = []

    for col in list(renamed.columns):
        unit = _infer_epoch_unit(str(col), renamed[col])
        if unit is None:
            continue

        suffix = f"_{unit}"
        target = str(col) if str(col).endswith(suffix) else f"{col}{suffix}"
        rename_map[str(col)] = target
        epoch_cols.append((target, unit))

    if rename_map:
        renamed = renamed.rename(columns=rename_map)

    for epoch_col, unit in epoch_cols:
        if unit == "ms" and epoch_col.endswith("_ms"):
            utc_col = f"{epoch_col[:-3]}_utc"
        elif unit == "ns" and epoch_col.endswith("_ns"):
            utc_col = f"{epoch_col[:-3]}_utc"
        else:
            utc_col = f"{epoch_col}_utc"

        if utc_col in renamed.columns:
            continue

        renamed[utc_col] = pd.to_datetime(
            renamed[epoch_col],
            unit=unit,
            utc=True,
            errors="coerce",
        )

    return renamed


def normalize_records(records: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    flattened = [flatten_record(record) for record in records]
    if not flattened:
        return pd.DataFrame()

    df = pd.DataFrame(flattened)
    return _normalize_epoch_columns(df)
