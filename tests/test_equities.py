"""Tests for the API-first ``agora.equities`` surface.

`agora` is a thin client over the Massive REST API. Downstream packages
own caching/storage, so these tests mock ``MassiveClient.rest`` and
never make a real network call. The pure helpers (``_resolve_dates``,
``_pivot_*``, ``_apply_split_adjustment``) are exercised directly with
synthetic DataFrames.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from agora import equities
from agora.equities import market

# ── Surface tests (always run) ──────────────────────────────────────


def test_equities_namespace_exposed_at_top_level() -> None:
    import agora

    assert agora.equities is equities
    assert "equities" in agora.__all__


def test_equities_public_surface() -> None:
    expected = {
        # market
        "get_daily_prices", "get_daily_returns", "get_volume",
        "get_daily_grouped", "get_snapshot",
        # reference
        "get_tickers", "get_ticker_details",
        # company classification (now implemented)
        "get_industry", "get_sector",
        # company (still stubs — Benzinga)
        "get_major_news", "get_earnings",
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
# _apply_split_adjustment is no longer in the default code path but
# stays exported as a callable utility for client-side adjustment.


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


# ── Fakes for mocked REST calls ─────────────────────────────────────


class FakeAgg:
    """Stand-in for the SDK's Agg bar object (one row of OHLCV)."""

    def __init__(self, ts_ms: int, open: float, high: float, low: float,
                 close: float, volume: float, vwap: float | None = None,
                 transactions: int | None = None):
        self.timestamp = ts_ms
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.vwap = vwap
        self.transactions = transactions


class FakeGroupedAgg:
    """Stand-in for grouped daily aggs (no per-row timestamp; ticker on each)."""

    def __init__(self, ticker: str, open: float, high: float, low: float,
                 close: float, volume: float, vwap: float | None = None,
                 transactions: int | None = None):
        self.ticker = ticker
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.vwap = vwap
        self.transactions = transactions


def _ms(date: str) -> int:
    """ISO date → epoch milliseconds (UTC midnight)."""
    return int(pd.Timestamp(date, tz="UTC").timestamp() * 1000)


# ── get_daily_prices (mocked REST) ──────────────────────────────────


class TestGetDailyPricesRest:
    def _bars_for(self, dates: list[str], close_start: float = 100.0) -> list:
        return [
            FakeAgg(
                ts_ms=_ms(d),
                open=close_start + i, high=close_start + i + 2,
                low=close_start + i - 1, close=close_start + i + 1,
                volume=1_000_000 + i * 1000,
                vwap=close_start + i + 0.5,
                transactions=10_000,
            )
            for i, d in enumerate(dates)
        ]

    def test_single_ticker_close(self) -> None:
        fake = MagicMock()
        fake.rest.get_aggregates.return_value = self._bars_for(
            ["2024-01-02", "2024-01-03", "2024-01-04"]
        )
        out = equities.get_daily_prices(
            "AAPL", start="2024-01-02", end="2024-01-04", client=fake,
        )
        assert list(out.columns) == ["AAPL"]
        assert len(out) == 3
        # close values are start+1, start+2, start+3 → 101, 102, 103
        assert out["AAPL"].tolist() == [101.0, 102.0, 103.0]

    def test_basket_close(self) -> None:
        fake = MagicMock()
        fake.rest.get_aggregates.side_effect = lambda ticker, **kw: (
            self._bars_for(["2024-01-02", "2024-01-03"], close_start=100.0)
            if ticker == "AAPL" else
            self._bars_for(["2024-01-02", "2024-01-03"], close_start=200.0)
        )
        out = equities.get_daily_prices(
            ["AAPL", "MSFT"], start="2024-01-02", end="2024-01-03",
            client=fake,
        )
        assert list(out.columns) == ["AAPL", "MSFT"]
        assert out.loc[pd.Timestamp("2024-01-02"), "AAPL"] == 101.0
        assert out.loc[pd.Timestamp("2024-01-02"), "MSFT"] == 201.0

    def test_multi_field_returns_multiindex(self) -> None:
        fake = MagicMock()
        fake.rest.get_aggregates.return_value = self._bars_for(
            ["2024-01-02"]
        )
        out = equities.get_daily_prices(
            "AAPL", start="2024-01-02", end="2024-01-02",
            fields=("open", "close", "volume"),
            client=fake,
        )
        assert ("open", "AAPL") in out.columns
        assert ("close", "AAPL") in out.columns
        assert ("volume", "AAPL") in out.columns

    def test_unknown_ticker_returns_empty(self) -> None:
        fake = MagicMock()
        fake.rest.get_aggregates.return_value = []  # API returned no bars
        out = equities.get_daily_prices(
            "NOTAREALTICKER", start="2024-01-02", end="2024-01-04",
            client=fake,
        )
        assert out.empty

    def test_failed_ticker_propagates_via_attrs(self) -> None:
        from agora.errors import MassiveAPIError

        fake = MagicMock()
        fake.rest.get_aggregates.side_effect = lambda ticker, **kw: (
            self._bars_for(["2024-01-02"]) if ticker == "AAPL"
            else (_ for _ in ()).throw(MassiveAPIError("simulated"))
        )
        out = equities.get_daily_prices(
            ["AAPL", "BROKEN"], start="2024-01-02", end="2024-01-02",
            client=fake, strict=False,
        )
        assert "BROKEN" in out.attrs["failed_tickers"]
        assert "AAPL" in out.columns

    def test_strict_mode_raises_on_failure(self) -> None:
        from agora.errors import MassiveAPIError

        fake = MagicMock()
        fake.rest.get_aggregates.side_effect = MassiveAPIError("simulated")
        with pytest.raises(MassiveAPIError):
            equities.get_daily_prices(
                "AAPL", start="2024-01-02", end="2024-01-02",
                client=fake, strict=True,
            )


# ── get_daily_returns (mocked REST) ─────────────────────────────────


class TestGetDailyReturnsRest:
    def test_simple_returns(self) -> None:
        fake = MagicMock()
        fake.rest.get_aggregates.return_value = [
            FakeAgg(ts_ms=_ms("2024-01-02"), open=0, high=0, low=0,
                    close=100.0, volume=0),
            FakeAgg(ts_ms=_ms("2024-01-03"), open=0, high=0, low=0,
                    close=110.0, volume=0),
        ]
        rets = equities.get_daily_returns(
            "AAPL", start="2024-01-02", end="2024-01-03",
            method="simple", client=fake,
        )
        # 100→110 is +10%
        assert rets.iloc[-1]["AAPL"] == pytest.approx(0.10)

    def test_log_returns(self) -> None:
        import numpy as np

        fake = MagicMock()
        fake.rest.get_aggregates.return_value = [
            FakeAgg(ts_ms=_ms("2024-01-02"), open=0, high=0, low=0,
                    close=100.0, volume=0),
            FakeAgg(ts_ms=_ms("2024-01-03"), open=0, high=0, low=0,
                    close=110.0, volume=0),
        ]
        rets = equities.get_daily_returns(
            "AAPL", start="2024-01-02", end="2024-01-03",
            method="log", client=fake,
        )
        assert rets.iloc[-1]["AAPL"] == pytest.approx(np.log(110.0 / 100.0))


# ── get_volume (mocked REST) ────────────────────────────────────────


class TestGetVolumeRest:
    def test_basic_volume(self) -> None:
        fake = MagicMock()
        fake.rest.get_aggregates.side_effect = lambda ticker, **kw: [
            FakeAgg(ts_ms=_ms("2024-01-02"), open=0, high=0, low=0,
                    close=100.0,
                    volume=1_000_000 if ticker == "AAPL" else 500_000),
        ]
        vol = equities.get_volume(
            ["AAPL", "MSFT"], start="2024-01-02", end="2024-01-02",
            client=fake,
        )
        assert vol.loc[pd.Timestamp("2024-01-02"), "AAPL"] == 1_000_000
        assert vol.loc[pd.Timestamp("2024-01-02"), "MSFT"] == 500_000


# ── get_daily_grouped (mocked REST) ─────────────────────────────────


class TestGetDailyGrouped:
    def test_returns_one_row_per_ticker(self) -> None:
        fake = MagicMock()
        fake.rest.get_grouped_daily_aggs.return_value = [
            FakeGroupedAgg("AAPL", 100, 105, 99, 103, 1_000_000, vwap=102.0),
            FakeGroupedAgg("MSFT", 200, 210, 198, 205, 500_000, vwap=204.0),
            FakeGroupedAgg("NVDA", 50, 52, 49, 51, 2_000_000, vwap=50.5),
        ]
        df = equities.get_daily_grouped("2024-01-03", client=fake)
        assert len(df) == 3
        assert set(df["ticker"]) == {"AAPL", "MSFT", "NVDA"}
        # All rows have the same date
        assert (df["date"] == pd.Timestamp("2024-01-03")).all()
        # Schema check
        for col in ("ticker", "date", "open", "high", "low", "close",
                    "volume", "vwap", "transactions"):
            assert col in df.columns

    def test_empty_for_no_results(self) -> None:
        fake = MagicMock()
        fake.rest.get_grouped_daily_aggs.return_value = []
        df = equities.get_daily_grouped("2024-01-06", client=fake)  # weekend
        assert df.empty
        # Empty frame still has the schema columns
        for col in ("ticker", "date", "open", "high", "low", "close",
                    "volume", "vwap", "transactions"):
            assert col in df.columns

    def test_ticker_filter_applied_after_pull(self) -> None:
        fake = MagicMock()
        fake.rest.get_grouped_daily_aggs.return_value = [
            FakeGroupedAgg("AAPL", 100, 105, 99, 103, 1_000_000),
            FakeGroupedAgg("MSFT", 200, 210, 198, 205, 500_000),
            FakeGroupedAgg("NVDA", 50, 52, 49, 51, 2_000_000),
        ]
        df = equities.get_daily_grouped(
            "2024-01-03", tickers=["AAPL", "MSFT"], client=fake,
        )
        assert set(df["ticker"]) == {"AAPL", "MSFT"}
        # Bulk call still happened — we filter client-side
        fake.rest.get_grouped_daily_aggs.assert_called_once()

    def test_ticker_filter_normalizes_case(self) -> None:
        fake = MagicMock()
        fake.rest.get_grouped_daily_aggs.return_value = [
            FakeGroupedAgg("AAPL", 100, 100, 100, 100, 1),
        ]
        df = equities.get_daily_grouped(
            "2024-01-03", tickers=["aapl"], client=fake,
        )
        assert df.iloc[0]["ticker"] == "AAPL"

    def test_passes_adjusted_and_otc_through(self) -> None:
        fake = MagicMock()
        fake.rest.get_grouped_daily_aggs.return_value = []
        equities.get_daily_grouped(
            "2024-01-03", adjusted=False, include_otc=True, client=fake,
        )
        fake.rest.get_grouped_daily_aggs.assert_called_once_with(
            "2024-01-03", adjusted=False, include_otc=True,
        )


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


class TestBenzingaStubsStillRaise:
    """Benzinga-entitled features remain stubs until the add-on is enabled."""

    def test_benzinga_stubs_raise_about_entitlement(self) -> None:
        with pytest.raises(NotImplementedError, match="Benzinga"):
            equities.get_major_news(["AAPL"])
        with pytest.raises(NotImplementedError, match="Benzinga"):
            equities.get_earnings(["AAPL"])


# ── Corporate actions: dividends (mocked REST) ──────────────────────


class FakeDividend:
    def __init__(self, ticker: str, ex_date: str, cash_amount: float,
                 pay_date: str | None = None, record_date: str | None = None,
                 declaration_date: str | None = None,
                 currency: str = "USD", frequency: int = 4,
                 dividend_type: str = "CD"):
        self.ticker = ticker
        self.ex_dividend_date = ex_date
        self.pay_date = pay_date
        self.record_date = record_date
        self.declaration_date = declaration_date
        self.cash_amount = cash_amount
        self.currency = currency
        self.frequency = frequency
        self.dividend_type = dividend_type


class TestGetDividends:
    def test_single_ticker_calls_list_dividends_with_ticker(self) -> None:
        fake = MagicMock()
        fake.rest.list_dividends.return_value = [
            FakeDividend("AAPL", "2024-02-09", 0.24),
            FakeDividend("AAPL", "2024-05-10", 0.24),
        ]
        df = equities.cax.get_dividends("AAPL", client=fake)
        fake.rest.list_dividends.assert_called_once_with(
            ticker="AAPL", ex_dividend_date_gte=None, ex_dividend_date_lte=None,
        )
        assert (df["ticker"] == "AAPL").all()
        assert len(df) == 2
        # Schema columns we expect
        for col in (
            "ticker", "ex_dividend_date", "pay_date", "record_date",
            "declaration_date", "cash_amount", "currency",
            "frequency", "dividend_type",
        ):
            assert col in df.columns

    def test_basket_iterates_per_ticker(self) -> None:
        fake = MagicMock()
        fake.rest.list_dividends.side_effect = lambda **kw: [
            FakeDividend(kw["ticker"], "2024-02-09", 0.24),
        ]
        df = equities.cax.get_dividends(["AAPL", "MSFT"], client=fake)
        assert fake.rest.list_dividends.call_count == 2
        assert set(df["ticker"]) == {"AAPL", "MSFT"}

    def test_no_tickers_calls_bulk(self) -> None:
        fake = MagicMock()
        fake.rest.list_dividends.return_value = [
            FakeDividend("AAPL", "2024-02-09", 0.24),
            FakeDividend("MSFT", "2024-02-15", 0.75),
        ]
        df = equities.cax.get_dividends(client=fake)
        fake.rest.list_dividends.assert_called_once_with(
            ex_dividend_date_gte=None, ex_dividend_date_lte=None,
        )
        assert len(df) == 2

    def test_date_range_passes_through_as_filters(self) -> None:
        fake = MagicMock()
        fake.rest.list_dividends.return_value = []
        equities.cax.get_dividends(
            "AAPL", start="2024-01-01", end="2024-12-31", client=fake,
        )
        fake.rest.list_dividends.assert_called_once_with(
            ticker="AAPL",
            ex_dividend_date_gte="2024-01-01",
            ex_dividend_date_lte="2024-12-31",
        )

    def test_unknown_ticker_returns_empty(self) -> None:
        fake = MagicMock()
        fake.rest.list_dividends.return_value = []
        df = equities.cax.get_dividends("NOTAREAL", client=fake)
        assert df.empty
        # Empty frame still has the schema columns
        for col in (
            "ticker", "ex_dividend_date", "pay_date", "record_date",
            "declaration_date", "cash_amount", "currency",
            "frequency", "dividend_type",
        ):
            assert col in df.columns

    def test_ticker_is_normalized_to_uppercase(self) -> None:
        fake = MagicMock()
        fake.rest.list_dividends.return_value = []
        equities.cax.get_dividends("aapl", client=fake)
        fake.rest.list_dividends.assert_called_once_with(
            ticker="AAPL", ex_dividend_date_gte=None, ex_dividend_date_lte=None,
        )

    def test_results_sorted_by_ex_date_then_ticker(self) -> None:
        fake = MagicMock()
        fake.rest.list_dividends.return_value = [
            FakeDividend("MSFT", "2024-05-15", 0.75),
            FakeDividend("AAPL", "2024-02-09", 0.24),
            FakeDividend("AAPL", "2024-05-10", 0.24),
        ]
        df = equities.cax.get_dividends(client=fake)
        ex_dates = df["ex_dividend_date"].tolist()
        assert ex_dates == sorted(ex_dates)


# ── Corporate actions: splits (mocked REST) ─────────────────────────


class FakeSplit:
    def __init__(self, ticker: str, exec_date: str,
                 split_from: int, split_to: int):
        self.ticker = ticker
        self.execution_date = exec_date
        self.split_from = split_from
        self.split_to = split_to


class TestGetSplits:
    def test_single_ticker(self) -> None:
        fake = MagicMock()
        fake.rest.list_splits.return_value = [
            FakeSplit("AAPL", "2020-08-31", 1, 4),
        ]
        df = equities.cax.get_splits("AAPL", client=fake)
        fake.rest.list_splits.assert_called_once_with(
            ticker="AAPL", execution_date_gte=None, execution_date_lte=None,
        )
        assert (df["ticker"] == "AAPL").all()
        for col in ("ticker", "execution_date", "split_from", "split_to"):
            assert col in df.columns

    def test_basket(self) -> None:
        fake = MagicMock()
        fake.rest.list_splits.side_effect = lambda **kw: [
            FakeSplit(kw["ticker"], "2020-08-31", 1, 4),
        ]
        df = equities.cax.get_splits(["AAPL", "TSLA"], client=fake)
        assert fake.rest.list_splits.call_count == 2
        assert set(df["ticker"]) == {"AAPL", "TSLA"}

    def test_date_range_passes_through(self) -> None:
        fake = MagicMock()
        fake.rest.list_splits.return_value = []
        equities.cax.get_splits(
            start="2020-01-01", end="2020-12-31", client=fake,
        )
        fake.rest.list_splits.assert_called_once_with(
            execution_date_gte="2020-01-01",
            execution_date_lte="2020-12-31",
        )

    def test_aapl_4_for_1_in_2020(self) -> None:
        """The classic AAPL 4:1 split — sanity-check shape."""
        fake = MagicMock()
        fake.rest.list_splits.return_value = [
            FakeSplit("AAPL", "2020-08-31", 1, 4),
        ]
        df = equities.cax.get_splits(
            "AAPL", start="2020-01-01", end="2020-12-31", client=fake,
        )
        assert not df.empty
        row = df.iloc[0]
        assert row["execution_date"] == pd.Timestamp("2020-08-31")
        assert int(row["split_from"]) == 1
        assert int(row["split_to"]) == 4

    def test_unknown_ticker_returns_empty(self) -> None:
        fake = MagicMock()
        fake.rest.list_splits.return_value = []
        df = equities.cax.get_splits("NOTAREAL", client=fake)
        assert df.empty
        for col in ("ticker", "execution_date", "split_from", "split_to"):
            assert col in df.columns


# ── Reference (mocked REST) ─────────────────────────────────────────


class FakeTickerRecord:
    """Stand-in for SDK list_tickers ticker objects."""

    def __init__(self, ticker, name, **kwargs):
        self.ticker = ticker
        self.name = name
        self.market = kwargs.get("market", "stocks")
        self.locale = kwargs.get("locale", "us")
        self.primary_exchange = kwargs.get("primary_exchange", "XNAS")
        self.type = kwargs.get("type", "CS")
        self.active = kwargs.get("active", True)
        self.currency_name = kwargs.get("currency_name", "usd")
        self.cik = kwargs.get("cik")
        self.composite_figi = kwargs.get("composite_figi")
        self.share_class_figi = kwargs.get("share_class_figi")
        self.last_updated_utc = kwargs.get("last_updated_utc")
        self.delisted_utc = kwargs.get("delisted_utc")


class FakeTickerDetails:
    """Stand-in for SDK get_ticker_details detail objects."""

    def __init__(self, ticker, **kwargs):
        # Identity
        self.ticker = ticker
        self.name = kwargs.get("name", f"{ticker} Inc.")
        self.cik = kwargs.get("cik")
        self.composite_figi = kwargs.get("composite_figi")
        self.share_class_figi = kwargs.get("share_class_figi")
        self.ticker_root = kwargs.get("ticker_root", ticker)
        self.ticker_suffix = kwargs.get("ticker_suffix")
        # Classification
        self.market = kwargs.get("market", "stocks")
        self.locale = kwargs.get("locale", "us")
        self.primary_exchange = kwargs.get("primary_exchange", "XNAS")
        self.type = kwargs.get("type", "CS")
        self.active = kwargs.get("active", True)
        self.currency_name = kwargs.get("currency_name", "usd")
        self.sic_code = kwargs.get("sic_code")
        self.sic_description = kwargs.get("sic_description")
        # Sizing
        self.market_cap = kwargs.get("market_cap")
        self.share_class_shares_outstanding = kwargs.get(
            "share_class_shares_outstanding"
        )
        self.weighted_shares_outstanding = kwargs.get(
            "weighted_shares_outstanding"
        )
        self.round_lot = kwargs.get("round_lot", 100)
        self.total_employees = kwargs.get("total_employees")
        # Profile
        self.description = kwargs.get("description")
        self.homepage_url = kwargs.get("homepage_url")
        self.list_date = kwargs.get("list_date")
        self.delisted_utc = kwargs.get("delisted_utc")
        self.phone_number = kwargs.get("phone_number")
        self.address = kwargs.get("address")
        self.branding = kwargs.get("branding")


class TestGetTickers:
    def test_basic_call(self) -> None:
        fake = MagicMock()
        fake.rest.list_tickers.return_value = [
            FakeTickerRecord("AAPL", "Apple Inc.", composite_figi="BBG_AAPL"),
            FakeTickerRecord("MSFT", "Microsoft Corp.", composite_figi="BBG_MSFT"),
        ]
        df = equities.get_tickers(market="stocks", type="CS", client=fake)
        assert len(df) == 2
        assert set(df["ticker"]) == {"AAPL", "MSFT"}
        # Schema check
        for col in (
            "ticker", "name", "market", "locale", "primary_exchange",
            "type", "active", "currency_name", "cik", "composite_figi",
            "share_class_figi", "last_updated_utc", "delisted_utc",
        ):
            assert col in df.columns

    def test_filters_pass_through(self) -> None:
        fake = MagicMock()
        fake.rest.list_tickers.return_value = []
        equities.get_tickers(
            market="stocks", type="ETF", active=False, search="foo",
            cik="1234", date="2024-01-03",
            sort="name", order="desc", limit=500,
            client=fake,
        )
        fake.rest.list_tickers.assert_called_once_with(
            market="stocks", type="ETF", active=False, search="foo",
            cik="1234", date="2024-01-03",
            sort="name", order="desc", limit=500,
        )

    def test_empty_records_returns_schema_frame(self) -> None:
        fake = MagicMock()
        fake.rest.list_tickers.return_value = []
        df = equities.get_tickers(client=fake)
        assert df.empty
        # Schema columns still present
        assert "ticker" in df.columns
        assert "composite_figi" in df.columns


class TestGetTickerDetails:
    def test_single_ticker(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_details.return_value = FakeTickerDetails(
            "AAPL",
            market_cap=3_000_000_000_000,
            share_class_shares_outstanding=15_000_000_000,
            sic_code="3571",
            sic_description="Electronic Computers",
            list_date="1980-12-12",
        )
        df = equities.get_ticker_details("AAPL", client=fake)
        fake.rest.get_ticker_details.assert_called_once_with("AAPL", date=None)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["ticker"] == "AAPL"
        assert row["market_cap"] == 3_000_000_000_000
        assert row["sic_code"] == "3571"
        assert row["sic_description"] == "Electronic Computers"
        # list_date coerced to datetime
        assert row["list_date"] == pd.Timestamp("1980-12-12")

    def test_basket_loops_per_ticker(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_details.side_effect = lambda t, **kw: (
            FakeTickerDetails(t, market_cap=1e12)
        )
        df = equities.get_ticker_details(["AAPL", "MSFT", "NVDA"], client=fake)
        assert fake.rest.get_ticker_details.call_count == 3
        assert set(df["ticker"]) == {"AAPL", "MSFT", "NVDA"}

    def test_date_parameter_passes_through(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_details.return_value = FakeTickerDetails("AAPL")
        equities.get_ticker_details("AAPL", date="2020-01-15", client=fake)
        fake.rest.get_ticker_details.assert_called_once_with(
            "AAPL", date="2020-01-15"
        )

    def test_ticker_normalized_to_uppercase(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_details.return_value = FakeTickerDetails("AAPL")
        equities.get_ticker_details("aapl", client=fake)
        fake.rest.get_ticker_details.assert_called_once_with("AAPL", date=None)

    def test_empty_ticker_basket_raises(self) -> None:
        fake = MagicMock()
        with pytest.raises(ValueError, match="must not be empty"):
            equities.get_ticker_details([], client=fake)

    def test_schema_present_when_no_results(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_details.return_value = None
        df = equities.get_ticker_details("AAPL", client=fake)
        assert df.empty
        # Schema preserved
        for col in ("ticker", "market_cap", "sic_code", "sic_description"):
            assert col in df.columns


# ── Classification (mocked, derived from get_ticker_details) ────────


class TestGetIndustry:
    def test_returns_sic_description_indexed_by_ticker(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_details.side_effect = lambda t, **kw: (
            FakeTickerDetails("AAPL", sic_code="3571",
                              sic_description="Electronic Computers")
            if t == "AAPL" else
            FakeTickerDetails("JPM", sic_code="6020",
                              sic_description="National Commercial Banks")
        )
        s = equities.get_industry(["AAPL", "JPM"], client=fake)
        assert s.name == "industry"
        assert s.loc["AAPL"] == "Electronic Computers"
        assert s.loc["JPM"] == "National Commercial Banks"

    def test_empty_basket_returns_empty_series(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_details.return_value = None
        s = equities.get_industry("ZZZZ", client=fake)
        assert s.empty
        assert s.name == "industry"


class TestGetSector:
    def test_maps_sic_code_to_division(self) -> None:
        # AAPL=3571 (Mfg), JPM=6020 (Finance), XOM=2911 (Mfg)
        fake = MagicMock()
        fake.rest.get_ticker_details.side_effect = lambda t, **kw: (
            FakeTickerDetails("AAPL", sic_code="3571")
            if t == "AAPL" else
            FakeTickerDetails("JPM", sic_code="6020")
            if t == "JPM" else
            FakeTickerDetails("XOM", sic_code="2911")
        )
        s = equities.get_sector(["AAPL", "JPM", "XOM"], client=fake)
        assert s.name == "sector"
        assert s.loc["AAPL"] == "Manufacturing"
        assert s.loc["JPM"] == "Finance, Insurance, Real Estate"
        assert s.loc["XOM"] == "Manufacturing"

    def test_unmapped_or_missing_sic_yields_none(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_details.side_effect = lambda t, **kw: (
            FakeTickerDetails("FOO", sic_code=None)
            if t == "FOO" else
            FakeTickerDetails("BAR", sic_code="abc")  # malformed
        )
        s = equities.get_sector(["FOO", "BAR"], client=fake)
        assert s.loc["FOO"] is None
        assert s.loc["BAR"] is None

    def test_division_boundary_codes(self) -> None:
        """Spot-check the division boundaries."""
        from agora.equities.company.classification import _sic_to_sector

        assert _sic_to_sector("0100") == "Agriculture, Forestry, Fishing"
        assert _sic_to_sector("0999") == "Agriculture, Forestry, Fishing"
        assert _sic_to_sector("1000") == "Mining"
        assert _sic_to_sector("3999") == "Manufacturing"
        assert _sic_to_sector("4000") == "Transportation, Communications, Utilities"
        assert _sic_to_sector("5000") == "Wholesale Trade"
        assert _sic_to_sector("5200") == "Retail Trade"
        assert _sic_to_sector("6000") == "Finance, Insurance, Real Estate"
        assert _sic_to_sector("7000") == "Services"
        assert _sic_to_sector("9100") == "Public Administration"
        assert _sic_to_sector("9900") == "Nonclassifiable"

    def test_int_sic_code_handled(self) -> None:
        from agora.equities.company.classification import _sic_to_sector

        assert _sic_to_sector(3571) == "Manufacturing"
