"""Tests for the ``agora.equities`` surface.

These cover the implemented `market.py` functions thoroughly. Stub
modules (reference, cax, company) get a "raises NotImplementedError"
test apiece — that fails loudly the moment someone *implements* a stub
and forgets to update the test, which is the right pressure.

Live-API tests (snapshot, REST source) are mocked so CI doesn't need
secrets and never makes a real network call.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from agora import equities
from agora.equities import market


# ── Fixtures ────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
STOCK_DIR = DATA_DIR / "stocks" / "daily"
REF_DIR = DATA_DIR / "reference"

requires_stocks = pytest.mark.skipif(
    not STOCK_DIR.exists() or not list(STOCK_DIR.glob("*.parquet")),
    reason="data/stocks/daily/*.parquet not present (run `agora-download stocks` first)",
)
requires_dividends = pytest.mark.skipif(
    not (REF_DIR / "dividends.parquet").exists(),
    reason="data/reference/dividends.parquet not present (run `agora-download reference` first)",
)
requires_splits = pytest.mark.skipif(
    not (REF_DIR / "splits.parquet").exists(),
    reason="data/reference/splits.parquet not present (run `agora-download reference` first)",
)


# ── Surface tests (always run) ──────────────────────────────────────

def test_equities_namespace_exposed_at_top_level() -> None:
    import agora

    assert agora.equities is equities
    assert "equities" in agora.__all__


def test_equities_public_surface() -> None:
    expected = {
        # market
        "get_daily_prices", "get_daily_returns", "get_volume", "get_snapshot",
        # reference
        "get_exchange", "get_currency", "get_country",
        "get_market_cap", "get_shares_out",
        # company
        "get_industry", "get_sector", "get_major_news", "get_earnings",
        # subpackages
        "cax", "company",
    }
    missing = expected - set(dir(equities))
    assert not missing, f"missing from agora.equities: {sorted(missing)}"


def test_cax_subpackage_surface() -> None:
    from agora.equities import cax

    assert callable(cax.get_dividends)
    assert callable(cax.get_splits)


def test_company_subpackage_surface() -> None:
    from agora.equities import company

    assert callable(company.get_industry)
    assert callable(company.get_sector)
    assert callable(company.get_major_news)
    assert callable(company.get_earnings)


# ── Helper-function tests (always run) ──────────────────────────────

class TestNormHelpers:
    def test_norm_tickers_str(self) -> None:
        assert market._norm_tickers("aapl") == ["AAPL"]

    def test_norm_tickers_list(self) -> None:
        assert market._norm_tickers([" aapl ", "msft", ""]) == ["AAPL", "MSFT"]

    def test_norm_tickers_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            market._norm_tickers([])

    def test_norm_fields_single(self) -> None:
        assert market._norm_fields("close") == (["close"], True)

    def test_norm_fields_multi(self) -> None:
        fields, single = market._norm_fields(["open", "close"])
        assert fields == ["open", "close"]
        assert single is False

    def test_norm_fields_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid field"):
            market._norm_fields("bogus")


class TestResolveDates:
    def test_period_1y(self) -> None:
        start, end = market._resolve_dates(None, None, "1y")
        # ISO date strings, end >= start
        assert len(start) == 10 and len(end) == 10
        assert start < end

    def test_period_ytd(self) -> None:
        start, end = market._resolve_dates(None, None, "ytd")
        assert start.endswith("-01-01")

    def test_explicit_start_end(self) -> None:
        s, e = market._resolve_dates("2024-01-01", "2024-12-31", None)
        assert s == "2024-01-01" and e == "2024-12-31"

    def test_period_with_explicit_dates_raises(self) -> None:
        with pytest.raises(ValueError, match="either period OR"):
            market._resolve_dates("2024-01-01", None, "1y")

    def test_invalid_period(self) -> None:
        with pytest.raises(ValueError, match="Invalid period"):
            market._resolve_dates(None, None, "100y")

    def test_missing_dates_no_period(self) -> None:
        with pytest.raises(ValueError, match="period or both"):
            market._resolve_dates(None, None, None)


# ── Split adjustment math (always run; uses synthetic data) ─────────

class TestSplitAdjustment:
    def test_no_splits_returns_unchanged(self) -> None:
        prices = pd.DataFrame([
            {"date": pd.Timestamp("2024-01-01"), "ticker": "AAPL",
             "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000},
        ])
        out = market._apply_split_adjustment(prices, pd.DataFrame())
        pd.testing.assert_frame_equal(out, prices)

    def test_4_for_1_split_pre_post(self) -> None:
        """A pre-split price of $400 with vol 100 should become $100 with vol 400
        after a 4-for-1 split. Post-split prices stay unchanged."""
        prices = pd.DataFrame([
            {"date": pd.Timestamp("2020-08-30"), "ticker": "AAPL",
             "open": 400, "high": 400, "low": 400, "close": 400, "volume": 100},
            {"date": pd.Timestamp("2020-09-01"), "ticker": "AAPL",
             "open": 100, "high": 100, "low": 100, "close": 100, "volume": 400},
        ])
        splits = pd.DataFrame([{
            "ticker": "AAPL",
            "execution_date": pd.Timestamp("2020-08-31"),
            "split_from": 1,
            "split_to": 4,
        }])
        out = market._apply_split_adjustment(prices, splits)
        # Pre-split row: prices /4, volume *4
        assert out.iloc[0]["close"] == 100
        assert out.iloc[0]["volume"] == 400
        # Post-split row: unchanged
        assert out.iloc[1]["close"] == 100
        assert out.iloc[1]["volume"] == 400

    def test_only_other_tickers_splits_dont_touch(self) -> None:
        prices = pd.DataFrame([
            {"date": pd.Timestamp("2020-01-01"), "ticker": "MSFT",
             "open": 200, "high": 200, "low": 200, "close": 200, "volume": 50},
        ])
        splits = pd.DataFrame([{
            "ticker": "AAPL",  # different ticker
            "execution_date": pd.Timestamp("2020-08-31"),
            "split_from": 1, "split_to": 4,
        }])
        out = market._apply_split_adjustment(prices, splits)
        assert out.iloc[0]["close"] == 200
        assert out.iloc[0]["volume"] == 50


# ── Pivot tests (always run; use synthetic data) ────────────────────

class TestPivot:
    @pytest.fixture
    def long_df(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"date": pd.Timestamp("2024-01-02"), "ticker": "AAPL",
             "open": 1, "close": 2, "volume": 100},
            {"date": pd.Timestamp("2024-01-02"), "ticker": "MSFT",
             "open": 3, "close": 4, "volume": 200},
            {"date": pd.Timestamp("2024-01-03"), "ticker": "AAPL",
             "open": 5, "close": 6, "volume": 300},
        ])

    def test_pivot_single_field(self, long_df) -> None:
        out = market._pivot_single(long_df, "close", ["AAPL", "MSFT"])
        assert list(out.columns) == ["AAPL", "MSFT"]
        assert out.loc[pd.Timestamp("2024-01-02"), "AAPL"] == 2
        assert out.loc[pd.Timestamp("2024-01-02"), "MSFT"] == 4
        assert pd.isna(out.loc[pd.Timestamp("2024-01-03"), "MSFT"])

    def test_pivot_multi_field(self, long_df) -> None:
        out = market._pivot_multi(long_df, ["close", "volume"], ["AAPL", "MSFT"])
        # MultiIndex columns: (field, ticker)
        assert ("close", "AAPL") in out.columns
        assert ("volume", "MSFT") in out.columns
        assert out.loc[pd.Timestamp("2024-01-02"), ("close", "AAPL")] == 2
        assert out.loc[pd.Timestamp("2024-01-02"), ("volume", "MSFT")] == 200


# ── End-to-end against local Parquet (skip if data missing) ─────────

class TestGetDailyPricesParquet:
    @requires_stocks
    def test_single_ticker_close(self) -> None:
        prices = equities.get_daily_prices(
            "AAPL", start="2025-01-01", end="2025-01-15", source="parquet",
        )
        assert not prices.empty
        assert list(prices.columns) == ["AAPL"]
        assert prices.index.name == "date"

    @requires_stocks
    def test_basket_close(self) -> None:
        prices = equities.get_daily_prices(
            ["AAPL", "MSFT"], start="2025-01-01", end="2025-01-31",
            source="parquet",
        )
        assert list(prices.columns) == ["AAPL", "MSFT"]
        assert (prices > 0).all().all()

    @requires_stocks
    def test_multi_field_returns_multiindex(self) -> None:
        out = equities.get_daily_prices(
            ["AAPL"], start="2025-01-01", end="2025-01-10",
            fields=("open", "close", "volume"),
            source="parquet",
        )
        assert ("close", "AAPL") in out.columns
        assert ("volume", "AAPL") in out.columns

    @requires_stocks
    def test_period_works(self) -> None:
        prices = equities.get_daily_prices(
            "SPY", period="1mo", source="parquet",
        )
        assert not prices.empty

    @requires_stocks
    def test_unknown_ticker_returns_empty(self) -> None:
        prices = equities.get_daily_prices(
            "NOTAREALTICKER1234", start="2025-01-01", end="2025-01-10",
            source="parquet",
        )
        assert prices.empty


class TestGetDailyReturnsParquet:
    @requires_stocks
    def test_simple_returns(self) -> None:
        rets = equities.get_daily_returns(
            "AAPL", start="2025-01-01", end="2025-01-31",
            method="simple", source="parquet",
        )
        assert not rets.empty
        # Returns are roughly in [-0.5, 0.5]
        assert rets["AAPL"].abs().max() < 1.0

    @requires_stocks
    def test_log_returns(self) -> None:
        rets = equities.get_daily_returns(
            "AAPL", start="2025-01-01", end="2025-01-31",
            method="log", source="parquet",
        )
        assert not rets.empty


class TestGetVolumeParquet:
    @requires_stocks
    def test_basic_volume(self) -> None:
        vol = equities.get_volume(
            ["AAPL", "MSFT"], start="2025-01-01", end="2025-01-10",
            source="parquet",
        )
        assert (vol > 0).all().all()


# ── Snapshot (mocked — no live API) ─────────────────────────────────

class FakeSnapshotDay:
    def __init__(self):
        self.open = 100.0
        self.high = 105.0
        self.low = 99.0
        self.close = 103.0
        self.volume = 1_000_000
        self.vwap = 102.0


class FakeSnapshotPrevDay:
    def __init__(self):
        self.close = 99.0
        self.volume = 950_000


class FakeSnapshot:
    def __init__(self, ticker: str):
        self.ticker = ticker
        self.todays_change = 4.0
        self.todays_change_perc = 4.04
        self.day = FakeSnapshotDay()
        self.prev_day = FakeSnapshotPrevDay()
        self.last_trade = None
        self.last_quote = None
        self.min = None
        self.updated = 1735689600_000_000_000  # 2025-01-01 in ns


class TestGetSnapshot:
    def test_single_ticker_uses_get_snapshot(self) -> None:
        fake_client = MagicMock()
        fake_client.rest.get_snapshot.return_value = FakeSnapshot("AAPL")

        df = equities.get_snapshot("AAPL", client=fake_client)

        fake_client.rest.get_snapshot.assert_called_once_with("AAPL")
        fake_client.rest.get_all_snapshots.assert_not_called()
        assert df.iloc[0]["ticker"] == "AAPL"
        assert df.iloc[0]["day_close"] == 103.0
        assert df.iloc[0]["prev_close"] == 99.0
        assert "updated_utc" in df.columns

    def test_basket_uses_get_all_snapshots_and_filters(self) -> None:
        fake_client = MagicMock()
        all_snaps = [
            FakeSnapshot("AAPL"),
            FakeSnapshot("MSFT"),
            FakeSnapshot("NVDA"),  # not requested
        ]
        fake_client.rest.get_all_snapshots.return_value = all_snaps

        df = equities.get_snapshot(["AAPL", "MSFT"], client=fake_client)

        fake_client.rest.get_all_snapshots.assert_called_once()
        fake_client.rest.get_snapshot.assert_not_called()
        assert set(df["ticker"]) == {"AAPL", "MSFT"}

    def test_no_tickers_returns_all(self) -> None:
        fake_client = MagicMock()
        all_snaps = [FakeSnapshot("A"), FakeSnapshot("B"), FakeSnapshot("C")]
        fake_client.rest.get_all_snapshots.return_value = all_snaps

        df = equities.get_snapshot(client=fake_client)
        assert len(df) == 3


# ── Stub modules raise clearly ──────────────────────────────────────

class TestStubsRaiseNotImplemented:
    """When these stop raising, that's the signal to update the tests."""

    def test_reference_stubs(self) -> None:
        for fn in (
            equities.get_exchange, equities.get_currency, equities.get_country,
            equities.get_market_cap, equities.get_shares_out,
        ):
            with pytest.raises(NotImplementedError):
                fn(["AAPL"])

    def test_company_stubs(self) -> None:
        for fn in (equities.get_industry, equities.get_sector):
            with pytest.raises(NotImplementedError):
                fn(["AAPL"])

    def test_benzinga_stubs_raise_about_entitlement(self) -> None:
        with pytest.raises(NotImplementedError, match="Benzinga"):
            equities.get_major_news(["AAPL"])
        with pytest.raises(NotImplementedError, match="Benzinga"):
            equities.get_earnings(["AAPL"])


# ── Corporate actions: dividends ────────────────────────────────────

class TestGetDividends:
    @requires_dividends
    def test_single_ticker(self) -> None:
        df = equities.cax.get_dividends("AAPL")
        assert not df.empty
        assert (df["ticker"] == "AAPL").all()
        # Schema columns we expect
        for col in (
            "ticker", "ex_dividend_date", "pay_date", "record_date",
            "declaration_date", "cash_amount", "currency",
            "frequency", "dividend_type",
        ):
            assert col in df.columns
        # Sorted by ex_dividend_date
        ex = df["ex_dividend_date"].dropna()
        assert (ex.diff().dropna() >= pd.Timedelta(0)).all()

    @requires_dividends
    def test_basket(self) -> None:
        df = equities.cax.get_dividends(["AAPL", "MSFT"])
        assert set(df["ticker"].unique()) <= {"AAPL", "MSFT"}
        assert not df.empty

    @requires_dividends
    def test_date_range_filters_on_ex_dividend(self) -> None:
        df = equities.cax.get_dividends(
            "AAPL", start="2024-01-01", end="2024-12-31"
        )
        if df.empty:
            pytest.skip("No AAPL dividends in 2024 in this dataset")
        assert df["ex_dividend_date"].min() >= pd.Timestamp("2024-01-01")
        assert df["ex_dividend_date"].max() <= pd.Timestamp("2024-12-31")

    @requires_dividends
    def test_no_filters_returns_everything(self) -> None:
        # Just make sure it doesn't blow up; the dataset has ~2M rows.
        df = equities.cax.get_dividends()
        assert not df.empty
        assert len(df) > 1000

    @requires_dividends
    def test_unknown_ticker_returns_empty(self) -> None:
        df = equities.cax.get_dividends("NOTAREALTICKER1234")
        assert df.empty

    @requires_dividends
    def test_ticker_is_normalized_to_uppercase(self) -> None:
        upper = equities.cax.get_dividends("AAPL")
        lower = equities.cax.get_dividends("aapl")
        pd.testing.assert_frame_equal(upper, lower)


# ── Corporate actions: splits ───────────────────────────────────────

class TestGetSplits:
    @requires_splits
    def test_single_ticker(self) -> None:
        df = equities.cax.get_splits("AAPL")
        assert not df.empty
        assert (df["ticker"] == "AAPL").all()
        for col in ("ticker", "execution_date", "split_from", "split_to"):
            assert col in df.columns

    @requires_splits
    def test_basket(self) -> None:
        df = equities.cax.get_splits(["AAPL", "TSLA", "NVDA"])
        assert set(df["ticker"].unique()) <= {"AAPL", "TSLA", "NVDA"}

    @requires_splits
    def test_date_range_filters_on_execution_date(self) -> None:
        df = equities.cax.get_splits(
            start="2020-01-01", end="2020-12-31"
        )
        if not df.empty:
            assert df["execution_date"].min() >= pd.Timestamp("2020-01-01")
            assert df["execution_date"].max() <= pd.Timestamp("2020-12-31")

    @requires_splits
    def test_aapl_4_for_1_in_2020(self) -> None:
        """Sanity check the dataset: AAPL had a 4:1 split on 2020-08-31."""
        df = equities.cax.get_splits("AAPL", start="2020-01-01", end="2020-12-31")
        assert not df.empty
        row = df[df["execution_date"] == pd.Timestamp("2020-08-31")]
        assert not row.empty
        assert int(row.iloc[0]["split_from"]) == 1
        assert int(row.iloc[0]["split_to"]) == 4

    @requires_splits
    def test_unknown_ticker_returns_empty(self) -> None:
        df = equities.cax.get_splits("NOTAREALTICKER1234")
        assert df.empty

