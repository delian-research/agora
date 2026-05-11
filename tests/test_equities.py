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
from agora.errors import MassiveAPIError

# ── Surface tests (always run) ──────────────────────────────────────


def test_equities_namespace_exposed_at_top_level() -> None:
    import agora

    assert agora.equities is equities
    assert "equities" in agora.__all__


def test_equities_public_surface() -> None:
    expected = {
        # market
        "get_daily_prices", "get_daily_returns", "get_volume",
        "get_daily_grouped", "get_previous_close", "get_snapshot",
        "get_last_price", "get_last_volume",
        "get_last_trade", "get_last_quote",
        "get_market_status", "get_market_holidays",
        # reference
        "get_tickers", "get_ticker_details",
        "get_ticker_types", "get_exchanges", "get_related_tickers",
        # company classification
        "get_industry", "get_sector",
        # company (still stubs — Benzinga)
        "get_major_news", "get_earnings",
        # subpackages
        "cax", "company", "etf", "fundamentals", "short_data",
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


class TestGetSnapshotResolvedColumns:
    """get_snapshot() surfaces last_price / last_volume / last_change_pct
    columns computed via the fallback chain — same semantics as
    get_last_price / get_last_volume, just baked into the DataFrame."""

    def test_resolved_columns_present(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = FakeSnapshot("AAPL")
        df = equities.get_snapshot("AAPL", client=fake)
        for col in ("last_price", "last_volume", "last_change_pct"):
            assert col in df.columns, f"missing column {col}"

    def test_last_price_uses_last_trade_when_present(self) -> None:
        fake = MagicMock()
        # _make_snapshot is defined further down in this file alongside
        # the last-value tests; reuse it here for the configurable fakes.
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", last_trade_price=234.5
        )
        df = equities.get_snapshot("AAPL", client=fake)
        assert df.iloc[0]["last_price"] == 234.5

    def test_last_price_falls_back_to_day_close(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", day_close=103.0, last_trade_price=None
        )
        df = equities.get_snapshot("AAPL", client=fake)
        assert df.iloc[0]["last_price"] == 103.0

    def test_last_price_falls_back_to_prev_close(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL",
            day_close=None,
            day_volume=None,
            prev_close=99.0,
            last_trade_price=None,
        )
        df = equities.get_snapshot("AAPL", client=fake)
        assert df.iloc[0]["last_price"] == 99.0

    def test_last_volume_uses_day_volume_when_present(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", day_volume=2_500_000
        )
        df = equities.get_snapshot("AAPL", client=fake)
        assert df.iloc[0]["last_volume"] == 2_500_000

    def test_last_volume_falls_back_to_prev_volume(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", day_volume=None, day_close=None, prev_volume=950_000
        )
        df = equities.get_snapshot("AAPL", client=fake)
        assert df.iloc[0]["last_volume"] == 950_000

    def test_last_change_pct_uses_last_price_vs_prev_close(self) -> None:
        """last_change_pct differs from todays_change_pct: it uses the
        resolved last_price (which can be last_trade_price), not just
        day_close. So pre/post-market moves are reflected."""
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", last_trade_price=108.9, day_close=103.0, prev_close=99.0
        )
        df = equities.get_snapshot("AAPL", client=fake)
        # (108.9 - 99.0) / 99.0 * 100 = 10.0
        assert df.iloc[0]["last_change_pct"] == pytest.approx(10.0)

    def test_last_change_pct_zero_when_falls_back_to_prev_close(self) -> None:
        """When last_price falls back to prev_close (no fresh data),
        last_change_pct is exactly 0.0 by formula — there's no fresh signal."""
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL",
            day_close=None,
            day_volume=None,
            prev_close=99.0,
            last_trade_price=None,
        )
        df = equities.get_snapshot("AAPL", client=fake)
        assert df.iloc[0]["last_price"] == 99.0
        assert df.iloc[0]["last_change_pct"] == pytest.approx(0.0)

    def test_basket_resolved_columns(self) -> None:
        """Resolved columns work over the bulk path too."""
        fake = MagicMock()
        fake.rest.get_all_snapshots.return_value = [
            _make_snapshot("AAPL", last_trade_price=234.5, prev_close=230.0),
            _make_snapshot("MSFT", last_trade_price=None, day_close=410.0,
                           prev_close=400.0),
        ]
        df = equities.get_snapshot(["AAPL", "MSFT"], client=fake)
        df = df.set_index("ticker")
        assert df.loc["AAPL", "last_price"] == 234.5
        assert df.loc["MSFT", "last_price"] == 410.0
        # (234.5 - 230.0) / 230.0 * 100
        assert df.loc["AAPL", "last_change_pct"] == pytest.approx(
            (234.5 - 230.0) / 230.0 * 100
        )


# ── Last-value helpers: get_last_price / get_last_volume ────────────


class FakeSnapshotLastTrade:
    def __init__(self, price: float, size: int = 100):
        self.price = price
        self.size = size


def _make_snapshot(
    ticker: str,
    *,
    day_close: float | None = 103.0,
    day_volume: int | None = 1_000_000,
    prev_close: float | None = 99.0,
    prev_volume: int | None = 950_000,
    last_trade_price: float | None = None,
):
    """Build a configurable FakeSnapshot for last-value tests."""
    s = FakeSnapshot(ticker)
    if day_close is None and day_volume is None:
        s.day = None
    else:
        s.day.close = day_close
        s.day.volume = day_volume
    if prev_close is None and prev_volume is None:
        s.prev_day = None
    else:
        s.prev_day.close = prev_close
        s.prev_day.volume = prev_volume
    if last_trade_price is not None:
        s.last_trade = FakeSnapshotLastTrade(price=last_trade_price)
    return s


class TestGetLastPrice:
    def test_single_ticker_uses_single_endpoint(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", last_trade_price=234.5
        )

        s = equities.get_last_price("AAPL", client=fake)

        fake.rest.get_snapshot.assert_called_once_with("AAPL")
        fake.rest.get_all_snapshots.assert_not_called()
        assert s.index.tolist() == ["AAPL"]
        assert s["AAPL"] == 234.5
        assert s.name == "price"
        assert s.attrs["source"]["AAPL"] == "last_trade"

    def test_basket_uses_bulk_endpoint_and_filters(self) -> None:
        fake = MagicMock()
        fake.rest.get_all_snapshots.return_value = [
            _make_snapshot("AAPL", last_trade_price=234.5),
            _make_snapshot("MSFT", last_trade_price=410.1),
            _make_snapshot("NVDA", last_trade_price=950.0),  # not requested
        ]

        s = equities.get_last_price(["AAPL", "MSFT"], client=fake)

        fake.rest.get_all_snapshots.assert_called_once()
        fake.rest.get_snapshot.assert_not_called()
        assert s.index.tolist() == ["AAPL", "MSFT"]
        assert s["AAPL"] == 234.5
        assert s["MSFT"] == 410.1

    def test_input_order_preserved(self) -> None:
        fake = MagicMock()
        # Return in different order than requested
        fake.rest.get_all_snapshots.return_value = [
            _make_snapshot("MSFT", last_trade_price=410.1),
            _make_snapshot("AAPL", last_trade_price=234.5),
        ]
        s = equities.get_last_price(["AAPL", "MSFT"], client=fake)
        assert s.index.tolist() == ["AAPL", "MSFT"]

    def test_fallback_to_day_close(self) -> None:
        """No last_trade → fall back to day_close."""
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", day_close=103.0, last_trade_price=None
        )
        s = equities.get_last_price("AAPL", client=fake)
        assert s["AAPL"] == 103.0
        assert s.attrs["source"]["AAPL"] == "day_close"

    def test_fallback_to_prev_close(self) -> None:
        """No last_trade and no day_close → fall back to prev_close."""
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL",
            day_close=None,
            day_volume=None,  # nukes the day object
            prev_close=99.0,
            last_trade_price=None,
        )
        s = equities.get_last_price("AAPL", client=fake)
        assert s["AAPL"] == 99.0
        assert s.attrs["source"]["AAPL"] == "prev_close"

    def test_missing_ticker_nonstrict_omitted(self) -> None:
        """Ticker not returned by bulk snapshot → omitted, listed in missing."""
        fake = MagicMock()
        fake.rest.get_all_snapshots.return_value = [
            _make_snapshot("AAPL", last_trade_price=234.5),
        ]
        s = equities.get_last_price(["AAPL", "NOTREAL"], client=fake)
        assert s.index.tolist() == ["AAPL"]
        assert s.attrs["missing_tickers"] == ["NOTREAL"]

    def test_missing_ticker_strict_raises(self) -> None:
        fake = MagicMock()
        fake.rest.get_all_snapshots.return_value = [
            _make_snapshot("AAPL", last_trade_price=234.5),
        ]
        with pytest.raises(KeyError, match="NOTREAL"):
            equities.get_last_price(["AAPL", "NOTREAL"], strict=True, client=fake)

    def test_single_ticker_rest_error_strict_raises(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.side_effect = MassiveAPIError("simulated")
        with pytest.raises(MassiveAPIError):
            equities.get_last_price("AAPL", strict=True, client=fake)

    def test_single_ticker_rest_error_nonstrict_listed_as_missing(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.side_effect = MassiveAPIError("simulated")
        s = equities.get_last_price("AAPL", client=fake)
        assert s.empty
        assert s.attrs["missing_tickers"] == ["AAPL"]

    def test_all_fields_null_nonstrict_omitted(self) -> None:
        """Ticker present but every column in the chain is null → missing."""
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL",
            day_close=None,
            day_volume=None,
            prev_close=None,
            prev_volume=None,
            last_trade_price=None,
        )
        s = equities.get_last_price("AAPL", client=fake)
        assert s.empty
        assert s.attrs["missing_tickers"] == ["AAPL"]

    def test_all_fields_null_strict_raises(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL",
            day_close=None,
            day_volume=None,
            prev_close=None,
            prev_volume=None,
            last_trade_price=None,
        )
        with pytest.raises(KeyError, match="AAPL"):
            equities.get_last_price("AAPL", strict=True, client=fake)

    def test_as_of_utc_populated(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", last_trade_price=234.5
        )
        s = equities.get_last_price("AAPL", client=fake)
        # FakeSnapshot.updated = 1735689600_000_000_000 ns → 2025-01-01 UTC
        as_of = s.attrs["as_of_utc"]["AAPL"]
        assert isinstance(as_of, pd.Timestamp)
        assert as_of.year == 2025

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError):
            equities.get_last_price([])


class TestGetLastVolume:
    def test_single_ticker_uses_single_endpoint(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", day_volume=2_500_000
        )

        s = equities.get_last_volume("AAPL", client=fake)

        fake.rest.get_snapshot.assert_called_once_with("AAPL")
        assert s["AAPL"] == 2_500_000
        assert s.name == "volume"
        assert s.attrs["source"]["AAPL"] == "day_volume"

    def test_basket_uses_bulk_endpoint(self) -> None:
        fake = MagicMock()
        fake.rest.get_all_snapshots.return_value = [
            _make_snapshot("AAPL", day_volume=2_500_000),
            _make_snapshot("MSFT", day_volume=1_800_000),
        ]
        s = equities.get_last_volume(["AAPL", "MSFT"], client=fake)
        fake.rest.get_all_snapshots.assert_called_once()
        fake.rest.get_snapshot.assert_not_called()
        assert s["AAPL"] == 2_500_000
        assert s["MSFT"] == 1_800_000

    def test_fallback_to_prev_volume(self) -> None:
        """No day_volume → fall back to prev_volume."""
        fake = MagicMock()
        fake.rest.get_snapshot.return_value = _make_snapshot(
            "AAPL", day_volume=None, day_close=None, prev_volume=950_000
        )
        s = equities.get_last_volume("AAPL", client=fake)
        assert s["AAPL"] == 950_000
        assert s.attrs["source"]["AAPL"] == "prev_volume"

    def test_last_trade_size_not_used(self) -> None:
        """Explicitly verify last_trade.size is NOT in the fallback chain
        (mixing units with day_volume would be wrong)."""
        fake = MagicMock()
        snap = _make_snapshot(
            "AAPL",
            day_volume=None,
            day_close=None,
            prev_volume=950_000,
            last_trade_price=234.5,  # has a last_trade with size=100
        )
        fake.rest.get_snapshot.return_value = snap
        s = equities.get_last_volume("AAPL", client=fake)
        # Should fall back to prev_volume (950_000), NOT use last_trade.size (100)
        assert s["AAPL"] == 950_000
        assert s.attrs["source"]["AAPL"] == "prev_volume"

    def test_missing_ticker_nonstrict_omitted(self) -> None:
        fake = MagicMock()
        fake.rest.get_all_snapshots.return_value = [
            _make_snapshot("AAPL", day_volume=2_500_000),
        ]
        s = equities.get_last_volume(["AAPL", "NOTREAL"], client=fake)
        assert s.index.tolist() == ["AAPL"]
        assert s.attrs["missing_tickers"] == ["NOTREAL"]

    def test_missing_ticker_strict_raises(self) -> None:
        fake = MagicMock()
        fake.rest.get_all_snapshots.return_value = [
            _make_snapshot("AAPL", day_volume=2_500_000),
        ]
        with pytest.raises(KeyError, match="NOTREAL"):
            equities.get_last_volume(
                ["AAPL", "NOTREAL"], strict=True, client=fake
            )

    def test_input_order_preserved(self) -> None:
        fake = MagicMock()
        fake.rest.get_all_snapshots.return_value = [
            _make_snapshot("MSFT", day_volume=1_800_000),
            _make_snapshot("AAPL", day_volume=2_500_000),
        ]
        s = equities.get_last_volume(["AAPL", "MSFT"], client=fake)
        assert s.index.tolist() == ["AAPL", "MSFT"]

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError):
            equities.get_last_volume([])


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


# ── Reference: get_exchanges (mocked REST) ──────────────────────────


class FakeExchange:
    def __init__(self, id, mic, name, **kwargs):
        self.id = id
        self.mic = mic
        self.name = name
        self.operating_mic = kwargs.get("operating_mic", mic)
        self.type = kwargs.get("type", "exchange")
        self.asset_class = kwargs.get("asset_class", "stocks")
        self.locale = kwargs.get("locale", "us")
        self.acronym = kwargs.get("acronym")
        self.participant_id = kwargs.get("participant_id")
        self.url = kwargs.get("url")


class TestGetExchanges:
    def test_basic_call(self) -> None:
        fake = MagicMock()
        fake.rest.get_exchanges.return_value = [
            FakeExchange(1, "XNAS", "Nasdaq Stock Market"),
            FakeExchange(2, "XNYS", "New York Stock Exchange"),
        ]
        df = equities.get_exchanges(client=fake)
        fake.rest.get_exchanges.assert_called_once_with(
            asset_class=None, locale=None,
        )
        assert len(df) == 2
        assert set(df["mic"]) == {"XNAS", "XNYS"}
        for col in (
            "id", "mic", "operating_mic", "name", "type", "asset_class",
            "locale", "acronym", "participant_id", "url",
        ):
            assert col in df.columns

    def test_filters_pass_through(self) -> None:
        fake = MagicMock()
        fake.rest.get_exchanges.return_value = []
        equities.get_exchanges(asset_class="stocks", locale="us", client=fake)
        fake.rest.get_exchanges.assert_called_once_with(
            asset_class="stocks", locale="us",
        )

    def test_empty_returns_schema_frame(self) -> None:
        fake = MagicMock()
        fake.rest.get_exchanges.return_value = []
        df = equities.get_exchanges(client=fake)
        assert df.empty
        assert "mic" in df.columns


# ── Reference: get_ticker_types (mocked REST) ───────────────────────


class FakeTickerType:
    def __init__(self, code, description, asset_class="stocks", locale="us"):
        self.code = code
        self.description = description
        self.asset_class = asset_class
        self.locale = locale


class TestGetTickerTypes:
    def test_basic_call(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_types.return_value = [
            FakeTickerType("CS", "Common Stock"),
            FakeTickerType("ETF", "Exchange Traded Fund"),
            FakeTickerType("ADRC", "American Depository Receipt Common"),
        ]
        df = equities.get_ticker_types(client=fake)
        fake.rest.get_ticker_types.assert_called_once_with(
            asset_class=None, locale=None,
        )
        assert len(df) == 3
        assert set(df["code"]) == {"CS", "ETF", "ADRC"}
        for col in ("code", "description", "asset_class", "locale"):
            assert col in df.columns

    def test_filters_pass_through(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_types.return_value = []
        equities.get_ticker_types(
            asset_class="stocks", locale="us", client=fake,
        )
        fake.rest.get_ticker_types.assert_called_once_with(
            asset_class="stocks", locale="us",
        )

    def test_empty_returns_schema_frame(self) -> None:
        fake = MagicMock()
        fake.rest.get_ticker_types.return_value = []
        df = equities.get_ticker_types(client=fake)
        assert df.empty
        assert "code" in df.columns


# ── Reference: get_related_tickers (mocked REST) ────────────────────


class FakeRelated:
    def __init__(self, ticker):
        self.ticker = ticker


class TestGetRelatedTickers:
    def test_basic_call(self) -> None:
        fake = MagicMock()
        fake.rest.get_related_companies.return_value = [
            FakeRelated("MSFT"),
            FakeRelated("GOOGL"),
            FakeRelated("AMZN"),
        ]
        df = equities.get_related_tickers("AAPL", client=fake)
        fake.rest.get_related_companies.assert_called_once_with("AAPL")
        assert len(df) == 3
        assert set(df["ticker"]) == {"MSFT", "GOOGL", "AMZN"}
        # Source ticker denormalized for easy basket merging
        assert (df["source_ticker"] == "AAPL").all()

    def test_ticker_normalized_to_uppercase(self) -> None:
        fake = MagicMock()
        fake.rest.get_related_companies.return_value = []
        equities.get_related_tickers("aapl", client=fake)
        fake.rest.get_related_companies.assert_called_once_with("AAPL")

    def test_empty_basket_raises(self) -> None:
        fake = MagicMock()
        with pytest.raises(ValueError, match="non-empty"):
            equities.get_related_tickers("", client=fake)
        with pytest.raises(ValueError, match="non-empty"):
            equities.get_related_tickers("   ", client=fake)

    def test_non_string_raises(self) -> None:
        fake = MagicMock()
        with pytest.raises(ValueError, match="non-empty"):
            equities.get_related_tickers(["AAPL"], client=fake)  # type: ignore[arg-type]

    def test_no_results_returns_schema_frame(self) -> None:
        fake = MagicMock()
        fake.rest.get_related_companies.return_value = []
        df = equities.get_related_tickers("AAPL", client=fake)
        assert df.empty
        for col in ("ticker", "source_ticker"):
            assert col in df.columns

    def test_drops_rows_with_missing_ticker(self) -> None:
        # SDK may occasionally return malformed rows; be defensive.
        fake = MagicMock()
        fake.rest.get_related_companies.return_value = [
            FakeRelated("MSFT"),
            FakeRelated(""),  # malformed
            FakeRelated(None),  # malformed
            FakeRelated("GOOGL"),
        ]
        df = equities.get_related_tickers("AAPL", client=fake)
        assert set(df["ticker"]) == {"MSFT", "GOOGL"}


# ── Market state + live ticks (mocked REST) ─────────────────────────


class FakeMarketStatus:
    def __init__(self):
        self.market = "open"
        self.after_hours = False
        self.early_hours = False
        self.server_time = "2025-05-10T15:30:00-04:00"
        self.exchanges = type("Exchanges", (), {"nasdaq": "open", "nyse": "open"})()
        self.currencies = type("Currencies", (), {"fx": "open"})()


class FakeMarketHoliday:
    def __init__(self, date, name, exchange="NASDAQ", status="closed"):
        self.date = date
        self.name = name
        self.exchange = exchange
        self.status = status
        self.open = None
        self.close = None


class TestGetMarketStatus:
    def test_returns_series_with_core_fields(self) -> None:
        fake = MagicMock()
        fake.rest.get_market_status.return_value = FakeMarketStatus()
        s = equities.get_market_status(client=fake)
        assert s["market"] == "open"
        assert s["after_hours"] is False
        assert s["server_time"]


class TestGetMarketHolidays:
    def test_returns_dataframe(self) -> None:
        fake = MagicMock()
        fake.rest.get_market_holidays.return_value = [
            FakeMarketHoliday("2024-12-25", "Christmas"),
            FakeMarketHoliday("2024-11-28", "Thanksgiving"),
        ]
        df = equities.get_market_holidays(client=fake)
        assert len(df) == 2
        assert "Christmas" in df["name"].values
        # Date column coerced to datetime
        assert pd.api.types.is_datetime64_any_dtype(df["date"])

    def test_empty_returns_schema_frame(self) -> None:
        fake = MagicMock()
        fake.rest.get_market_holidays.return_value = []
        df = equities.get_market_holidays(client=fake)
        assert df.empty
        for col in ("date", "name", "exchange", "status"):
            assert col in df.columns


class FakeLastTrade:
    def __init__(self, ticker="AAPL"):
        self.ticker = ticker
        self.price = 195.50
        self.size = 100
        self.exchange = 11
        self.conditions = [12]
        self.sip_timestamp = 1735689600_000_000_000
        self.participant_timestamp = 1735689599_900_000_000
        self.trf_timestamp = 0
        self.id = "abc123"
        self.sequence_number = 12345
        self.tape = 3
        self.correction = 0
        self.fractional_size = None
        self.trf_id = None


class FakeLastQuote:
    def __init__(self, ticker="AAPL"):
        self.ticker = ticker
        self.bid_price = 195.49
        self.bid_size = 5
        self.bid_exchange = 11
        self.ask_price = 195.51
        self.ask_size = 3
        self.ask_exchange = 12
        self.conditions = [0]
        self.indicators = []
        self.sip_timestamp = 1735689600_000_000_000
        self.participant_timestamp = 1735689599_900_000_000
        self.trf_timestamp = 0
        self.sequence_number = 12345
        self.tape = 3


class TestGetLastTrade:
    def test_returns_series_with_price_and_size(self) -> None:
        fake = MagicMock()
        fake.rest.get_last_trade.return_value = FakeLastTrade("AAPL")
        s = equities.get_last_trade("AAPL", client=fake)
        fake.rest.get_last_trade.assert_called_once_with("AAPL")
        assert s["ticker"] == "AAPL"
        assert s["price"] == 195.50
        assert s["size"] == 100
        # SIP timestamp surfaced as UTC
        assert "sip_timestamp_utc" in s.index

    def test_normalizes_ticker_case(self) -> None:
        fake = MagicMock()
        fake.rest.get_last_trade.return_value = FakeLastTrade("AAPL")
        equities.get_last_trade("aapl", client=fake)
        fake.rest.get_last_trade.assert_called_once_with("AAPL")

    def test_empty_ticker_raises(self) -> None:
        fake = MagicMock()
        with pytest.raises(ValueError, match="non-empty"):
            equities.get_last_trade("", client=fake)


class TestGetLastQuote:
    def test_returns_series_with_bid_ask(self) -> None:
        fake = MagicMock()
        fake.rest.get_last_quote.return_value = FakeLastQuote("AAPL")
        s = equities.get_last_quote("AAPL", client=fake)
        fake.rest.get_last_quote.assert_called_once_with("AAPL")
        assert s["bid_price"] == 195.49
        assert s["ask_price"] == 195.51
        assert "sip_timestamp_utc" in s.index

    def test_empty_ticker_raises(self) -> None:
        fake = MagicMock()
        with pytest.raises(ValueError, match="non-empty"):
            equities.get_last_quote("", client=fake)


class FakePrevClose:
    def __init__(self, ticker="AAPL"):
        self.ticker = ticker
        self.open = 100.0
        self.high = 105.0
        self.low = 99.0
        self.close = 103.0
        self.volume = 1_000_000
        self.vwap = 102.0
        self.timestamp = 1735689600_000  # 2025-01-01 ms


class TestGetPreviousClose:
    def test_basket(self) -> None:
        fake = MagicMock()
        fake.rest.get_previous_close_agg.side_effect = lambda t, **kw: FakePrevClose(t)
        df = equities.get_previous_close(["AAPL", "MSFT"], client=fake)
        assert set(df["ticker"]) == {"AAPL", "MSFT"}
        assert (df["close"] == 103.0).all()
        # Date converted from ms timestamp
        assert pd.api.types.is_datetime64_any_dtype(df["date"])

    def test_adjusted_passes_through(self) -> None:
        fake = MagicMock()
        fake.rest.get_previous_close_agg.return_value = FakePrevClose("AAPL")
        equities.get_previous_close("AAPL", adjusted=False, client=fake)
        fake.rest.get_previous_close_agg.assert_called_once_with("AAPL", adjusted=False)


# ── Fundamentals (mocked REST) ──────────────────────────────────────


class FakeBalanceSheet:
    def __init__(self, **kw):
        self.tickers = kw.get("tickers", "AAPL")
        self.cik = kw.get("cik", "0000320193")
        self.period_end = kw.get("period_end", "2024-09-28")
        self.filing_date = kw.get("filing_date", "2024-11-01")
        self.fiscal_year = kw.get("fiscal_year", 2024)
        self.fiscal_quarter = kw.get("fiscal_quarter", 4)
        self.timeframe = kw.get("timeframe", "annual")
        self.cash_and_equivalents = kw.get("cash_and_equivalents", 65_171_000_000)
        self.total_assets = kw.get("total_assets", 364_980_000_000)
        self.total_liabilities = kw.get("total_liabilities", 308_030_000_000)


class TestFundamentals:
    def test_balance_sheets_returns_dataframe(self) -> None:
        fake = MagicMock()
        fake.rest.list_financials_balance_sheets.return_value = [
            FakeBalanceSheet(tickers="AAPL", period_end="2023-09-30"),
            FakeBalanceSheet(tickers="AAPL", period_end="2024-09-28"),
        ]
        df = equities.fundamentals.get_balance_sheets(
            "AAPL", timeframe="annual", client=fake,
        )
        fake.rest.list_financials_balance_sheets.assert_called_once_with(
            tickers="AAPL", cik=None,
            period_end_gte=None, period_end_lte=None,
            timeframe="annual", limit=None,
        )
        assert len(df) == 2
        assert "cash_and_equivalents" in df.columns
        # period_end and filing_date should be datetime
        assert pd.api.types.is_datetime64_any_dtype(df["period_end"])

    def test_basket_joined_into_csv(self) -> None:
        fake = MagicMock()
        fake.rest.list_financials_balance_sheets.return_value = []
        equities.fundamentals.get_balance_sheets(
            ["AAPL", "MSFT"], client=fake,
        )
        # SDK expects comma-separated tickers
        fake.rest.list_financials_balance_sheets.assert_called_once_with(
            tickers="AAPL,MSFT", cik=None,
            period_end_gte=None, period_end_lte=None,
            timeframe=None, limit=None,
        )

    def test_date_filters_pass_as_period_end_gte_lte(self) -> None:
        fake = MagicMock()
        fake.rest.list_financials_income_statements.return_value = []
        equities.fundamentals.get_income_statements(
            "AAPL", start="2020-01-01", end="2024-12-31", client=fake,
        )
        fake.rest.list_financials_income_statements.assert_called_once_with(
            tickers="AAPL", cik=None,
            period_end_gte="2020-01-01", period_end_lte="2024-12-31",
            timeframe=None, limit=None,
        )

    def test_ratios_calls_with_uppercase_ticker(self) -> None:
        fake = MagicMock()
        fake.rest.list_financials_ratios.return_value = []
        equities.fundamentals.get_ratios("aapl", client=fake)
        fake.rest.list_financials_ratios.assert_called_once_with(
            ticker="AAPL", cik=None, limit=None,
        )

    def test_empty_returns_empty_frame(self) -> None:
        fake = MagicMock()
        fake.rest.list_financials_cash_flow_statements.return_value = []
        df = equities.fundamentals.get_cash_flow_statements("ZZZZ", client=fake)
        assert df.empty


# ── Short data (mocked REST) ────────────────────────────────────────


class FakeShortInterest:
    def __init__(self, ticker="AAPL", settlement_date="2024-12-15",
                 short_interest=120_000_000, days_to_cover=1.5,
                 avg_daily_volume=80_000_000):
        self.ticker = ticker
        self.settlement_date = settlement_date
        self.short_interest = short_interest
        self.days_to_cover = days_to_cover
        self.avg_daily_volume = avg_daily_volume


class FakeShortVolume:
    def __init__(self, ticker="AAPL", date="2024-12-15"):
        self.ticker = ticker
        self.date = date
        self.short_volume = 5_000_000
        self.total_volume = 50_000_000
        self.short_volume_ratio = 0.10


class FakeFloat:
    def __init__(self, ticker="AAPL"):
        self.ticker = ticker
        self.effective_date = "2024-12-15"
        self.free_float = 15_000_000_000
        self.free_float_percent = 99.9


class TestShortData:
    def test_short_interest(self) -> None:
        fake = MagicMock()
        fake.rest.list_short_interest.return_value = [
            FakeShortInterest(),
            FakeShortInterest(settlement_date="2024-12-31"),
        ]
        df = equities.short_data.get_short_interest("AAPL", client=fake)
        fake.rest.list_short_interest.assert_called_once_with(
            ticker="AAPL", settlement_date_gte=None,
            settlement_date_lte=None, limit=None,
        )
        assert len(df) == 2
        assert pd.api.types.is_datetime64_any_dtype(df["settlement_date"])

    def test_short_volume_date_filters(self) -> None:
        fake = MagicMock()
        fake.rest.list_short_volume.return_value = [FakeShortVolume()]
        equities.short_data.get_short_volume(
            "AAPL", start="2024-01-01", end="2024-12-31", client=fake,
        )
        fake.rest.list_short_volume.assert_called_once_with(
            ticker="AAPL", date_gte="2024-01-01", date_lte="2024-12-31",
            limit=None,
        )

    def test_floats(self) -> None:
        fake = MagicMock()
        fake.rest.list_stocks_floats.return_value = [FakeFloat()]
        df = equities.short_data.get_floats("AAPL", client=fake)
        assert df.iloc[0]["free_float_percent"] == 99.9


# ── ETF (mocked REST) ───────────────────────────────────────────────


class FakeConstituent:
    def __init__(self, composite="SPY", constituent="AAPL", weight=0.07):
        self.composite_ticker = composite
        self.constituent_ticker = constituent
        self.constituent_name = "Apple Inc."
        self.weight = weight
        self.shares_held = 100_000_000
        self.market_value = 19_500_000_000
        self.asset_class = "Equity"
        self.security_type = "CS"
        self.exchange = "XNAS"
        self.country_of_exchange = "US"
        self.currency_traded = "USD"
        self.isin = "US0378331005"
        self.sedol = "2046251"
        self.figi = "BBG000B9XRY4"
        self.us_code = None
        self.effective_date = "2024-12-15"
        self.processed_date = "2024-12-16"


class FakeFundFlow:
    def __init__(self, etf="SPY"):
        self.composite_ticker = etf
        self.effective_date = "2024-12-15"
        self.processed_date = "2024-12-16"
        self.fund_flow = 1_000_000_000
        self.nav = 600.0
        self.shares_outstanding = 1_000_000_000


class FakeProfile:
    def __init__(self, etf="SPY"):
        self.composite_ticker = etf
        self.issuer = "State Street"
        self.aum = 600_000_000_000
        self.effective_date = "2024-12-15"
        self.processed_date = "2024-12-16"


class FakeAnalytics:
    def __init__(self, etf="SPY"):
        self.composite_ticker = etf
        self.risk_total_score = 5.5
        self.reward_score = 7.0
        self.quant_total_score = 6.5
        self.quant_grade = "B"
        self.effective_date = "2024-12-15"
        self.processed_date = "2024-12-16"


class FakeTaxonomy:
    def __init__(self, etf="SPY"):
        self.composite_ticker = etf
        self.asset_class = "Equity"
        self.category = "Large Cap"
        self.focus = "Total Market"
        self.effective_date = "2024-12-15"
        self.processed_date = "2024-12-16"


class TestEtf:
    def test_constituents(self) -> None:
        fake = MagicMock()
        fake.rest.get_etf_global_constituents.return_value = [
            FakeConstituent("SPY", "AAPL", 0.07),
            FakeConstituent("SPY", "MSFT", 0.06),
        ]
        df = equities.etf.get_constituents("SPY", client=fake)
        fake.rest.get_etf_global_constituents.assert_called_once_with(
            composite_ticker="SPY", constituent_ticker=None,
            effective_date=None, effective_date_gte=None,
            effective_date_lte=None, limit=None,
        )
        assert set(df["constituent_ticker"]) == {"AAPL", "MSFT"}

    def test_constituents_normalizes_ticker_case(self) -> None:
        fake = MagicMock()
        fake.rest.get_etf_global_constituents.return_value = []
        equities.etf.get_constituents("spy", client=fake)
        fake.rest.get_etf_global_constituents.assert_called_once_with(
            composite_ticker="SPY", constituent_ticker=None,
            effective_date=None, effective_date_gte=None,
            effective_date_lte=None, limit=None,
        )

    def test_fund_flows(self) -> None:
        fake = MagicMock()
        fake.rest.get_etf_global_fund_flows.return_value = [FakeFundFlow()]
        df = equities.etf.get_fund_flows("SPY", client=fake)
        assert df.iloc[0]["fund_flow"] == 1_000_000_000

    def test_profiles(self) -> None:
        fake = MagicMock()
        fake.rest.get_etf_global_profiles.return_value = [FakeProfile()]
        df = equities.etf.get_profiles("SPY", client=fake)
        assert df.iloc[0]["issuer"] == "State Street"

    def test_analytics(self) -> None:
        fake = MagicMock()
        fake.rest.get_etf_global_analytics.return_value = [FakeAnalytics()]
        df = equities.etf.get_analytics("SPY", client=fake)
        assert df.iloc[0]["quant_grade"] == "B"

    def test_taxonomies(self) -> None:
        fake = MagicMock()
        fake.rest.get_etf_global_taxonomies.return_value = [FakeTaxonomy()]
        df = equities.etf.get_taxonomies("SPY", client=fake)
        assert df.iloc[0]["asset_class"] == "Equity"
