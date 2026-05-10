"""Behavioral tests for ``FlatFileLoader``.

Skipped when the Parquet store under ``data/`` isn't present so the suite
still passes on a fresh checkout that hasn't run ``agora-download all`` yet.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from agora.loaders.parquet import FlatFileLoader

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
STOCK_DIR = DATA_DIR / "stocks" / "daily"
FOREX_FILE = DATA_DIR / "forex" / "daily_usd.parquet"
REF_DIR = DATA_DIR / "reference"

requires_stocks = pytest.mark.skipif(
    not STOCK_DIR.exists() or not list(STOCK_DIR.glob("*.parquet")),
    reason="data/stocks/daily/*.parquet not present (run `agora-download stocks` first)",
)
requires_forex = pytest.mark.skipif(
    not FOREX_FILE.exists(),
    reason="data/forex/daily_usd.parquet not present (run `agora-download forex` first)",
)
requires_events = pytest.mark.skipif(
    not (REF_DIR / "ticker_events.parquet").exists(),
    reason="data/reference/ticker_events.parquet not present",
)


@pytest.fixture(scope="module")
def loader() -> FlatFileLoader:
    return FlatFileLoader()


@requires_stocks
def test_available_years_increasing(loader: FlatFileLoader) -> None:
    years = loader.available_stock_years()
    assert years == sorted(years)
    assert all(2000 < y < 2050 for y in years)


@requires_stocks
def test_get_stock_daily_filters_by_ticker_and_date(
    loader: FlatFileLoader,
) -> None:
    df = loader.get_stock_daily("AAPL", start="2024-01-01", end="2024-01-31")
    assert not df.empty
    assert (df["ticker"] == "AAPL").all()
    assert df["date"].min() >= pd.Timestamp("2024-01-01")
    assert df["date"].max() <= pd.Timestamp("2024-01-31")


@requires_stocks
def test_get_prices_returns_pivoted_matrix(loader: FlatFileLoader) -> None:
    prices = loader.get_prices(
        ["AAPL", "MSFT"], start="2024-06-01", end="2024-06-15"
    )
    assert list(prices.columns) == ["AAPL", "MSFT"]
    assert prices.index.name == "date"
    assert (prices.dtypes == "float64").all()


@requires_forex
def test_get_fx_rates_pivots_by_iso(loader: FlatFileLoader) -> None:
    rates = loader.get_fx_rates(["EUR", "GBP"], start="2025-01-01", end="2025-01-15")
    assert "EUR" in rates.columns
    assert "GBP" in rates.columns


@requires_events
def test_resolve_meta_to_fb_pre_rename(loader: FlatFileLoader) -> None:
    """The classic FB → META rename should round-trip via the security master."""
    assert loader.resolve_ticker("META", "2020-06-15") == "FB"
    assert loader.resolve_ticker("META", "2024-01-01") == "META"
