"""Smoke tests for ``agora.normalize.*``.

Each normalizer turns a raw API payload (or list of records) into a
DataFrame with stable columns. These tests feed each function a
representative fake payload and assert the output schema + a couple
of representative values.
"""

from __future__ import annotations

import pandas as pd

from agora.normalize import base, corporate_actions, ohlc, snapshot

# ── base ─────────────────────────────────────────────────────────────


class TestSnakeCase:
    def test_camel_to_snake(self) -> None:
        assert base.to_snake_case("camelCase") == "camel_case"
        assert base.to_snake_case("HTTPResponse") == "http_response"
        assert base.to_snake_case("already_snake") == "already_snake"

    def test_strips_special_chars(self) -> None:
        assert base.to_snake_case("foo.bar-baz") == "foo_bar_baz"


class TestFlattenRecord:
    def test_flat_record_unchanged(self) -> None:
        out = base.flatten_record({"a": 1, "b": 2})
        assert out == {"a": 1, "b": 2}

    def test_nested_dict_is_flattened_with_dot_path(self) -> None:
        out = base.flatten_record({"day": {"o": 100.0, "c": 105.0}})
        # The flatten implementation joins nested keys with "_" or ".".
        # We assert the keys exist with sensible naming, not the exact
        # separator (implementation detail).
        flat_values = list(out.values())
        assert 100.0 in flat_values
        assert 105.0 in flat_values


class TestNormalizeRecords:
    def test_empty_input_returns_empty_frame(self) -> None:
        df = base.normalize_records([])
        assert df.empty

    def test_camelcase_keys_become_snake(self) -> None:
        df = base.normalize_records([{"todaysChange": 1.0, "ticker": "AAPL"}])
        assert "todays_change" in df.columns
        assert "ticker" in df.columns


# ── ohlc ─────────────────────────────────────────────────────────────


class TestNormalizeGroupedDailyResults:
    def test_returns_expected_columns(self) -> None:
        payload = {
            "results": [
                {"T": "AAPL", "o": 100, "h": 105, "l": 99, "c": 103, "v": 1_000_000,
                 "vw": 102.0, "n": 5000, "t": 1735689600_000},
            ],
        }
        df = ohlc.normalize_grouped_daily_results(payload, "2025-01-01")
        assert {"date", "ticker", "open", "high", "low", "close", "volume", "vwap", "trades"} <= set(df.columns)
        assert df.iloc[0]["ticker"] == "AAPL"
        assert df.iloc[0]["close"] == 103

    def test_empty_results_returns_empty_with_schema(self) -> None:
        df = ohlc.normalize_grouped_daily_results({"results": []}, "2025-01-01")
        assert df.empty
        assert "ticker" in df.columns


class TestNormalizeOpenClose:
    def test_canonical_payload(self) -> None:
        payload = {
            "from": "2025-01-01",
            "symbol": "AAPL",
            "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0,
            "volume": 1_000_000, "preMarket": 99.5, "afterHours": 103.5, "otc": False,
        }
        df = ohlc.normalize_open_close(payload)
        assert df.iloc[0]["ticker"] == "AAPL"
        assert df.iloc[0]["pre_market"] == 99.5
        assert df.iloc[0]["after_hours"] == 103.5

    def test_empty_payload_returns_empty_with_schema(self) -> None:
        df = ohlc.normalize_open_close({})
        assert df.empty
        assert "after_hours" in df.columns


class TestNormalizeAggregateResults:
    def test_aggregates_to_dataframe(self) -> None:
        payload = {
            "results": [
                {"o": 100, "h": 105, "l": 99, "c": 103, "v": 1_000_000,
                 "vw": 102.0, "n": 5000, "t": 1735689600_000},
            ],
        }
        df = ohlc.normalize_aggregate_results(payload, ticker="AAPL")
        assert df.iloc[0]["ticker"] == "AAPL"
        assert df.iloc[0]["close"] == 103
        # ts_ms is preserved verbatim and ts_utc is parsed.
        assert df.iloc[0]["ts_ms"] == 1735689600_000
        assert pd.notna(df.iloc[0]["ts_utc"])

    def test_empty_returns_empty_with_schema(self) -> None:
        df = ohlc.normalize_aggregate_results({"results": []}, ticker="AAPL")
        assert df.empty
        assert "ticker" in df.columns


class TestNormalizePreviousDayResults:
    def test_uses_payload_ticker_when_present(self) -> None:
        payload = {
            "ticker": "AAPL", "adjusted": True,
            "results": [{"o": 100, "h": 105, "l": 99, "c": 103, "v": 1, "t": 1735689600_000}],
        }
        df = ohlc.normalize_previous_day_results(payload, fallback_ticker="FALLBACK")
        assert df.iloc[0]["ticker"] == "AAPL"
        assert bool(df.iloc[0]["adjusted"])  # numpy.bool_ → plain bool

    def test_falls_back_to_ticker_arg(self) -> None:
        payload = {
            "results": [{"o": 100, "h": 105, "l": 99, "c": 103, "v": 1, "t": 1735689600_000}],
        }
        df = ohlc.normalize_previous_day_results(payload, fallback_ticker="MSFT")
        assert df.iloc[0]["ticker"] == "MSFT"


# ── snapshot ─────────────────────────────────────────────────────────


def _fake_snapshot_record(ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "todaysChange": 4.0,
        "todaysChangePerc": 4.04,
        "updated": 1735689600_000_000_000,
        "day": {"o": 100, "h": 105, "l": 99, "c": 103, "v": 1_000_000, "vw": 102.0},
        "min": {"o": 102, "h": 103, "l": 102, "c": 103, "v": 100, "vw": 102.5, "t": 1735689600_000},
        "prevDay": {"o": 99, "h": 100, "l": 98, "c": 99, "v": 950_000, "vw": 99.5},
        "lastTrade": {"p": 103.0, "s": 100, "t": 1735689600_000_000_000},
        "lastQuote": {"P": 103.05, "p": 102.95, "t": 1735689600_000_000_000},
    }


class TestNormalizeSnapshotRecords:
    def test_basic_record(self) -> None:
        df = snapshot.normalize_snapshot_records([_fake_snapshot_record()])
        assert df.iloc[0]["ticker"] == "AAPL"
        assert df.iloc[0]["day_close"] == 103
        assert df.iloc[0]["prev_day_close"] == 99
        assert df.iloc[0]["last_trade_price"] == 103.0

    def test_empty_returns_empty_frame(self) -> None:
        df = snapshot.normalize_snapshot_records([])
        assert df.empty


class TestNormalizeSingleSnapshotPayload:
    def test_extracts_nested_ticker(self) -> None:
        payload = {"ticker": _fake_snapshot_record("MSFT")}
        df = snapshot.normalize_single_snapshot_payload(payload)
        assert df.iloc[0]["ticker"] == "MSFT"

    def test_missing_ticker_returns_empty(self) -> None:
        df = snapshot.normalize_single_snapshot_payload({})
        assert df.empty


class TestNormalizeFullSnapshotPayload:
    def test_multiple_tickers(self) -> None:
        payload = {"tickers": [_fake_snapshot_record("AAPL"), _fake_snapshot_record("MSFT")]}
        df = snapshot.normalize_full_snapshot_payload(payload)
        assert set(df["ticker"]) == {"AAPL", "MSFT"}


# ── corporate_actions ────────────────────────────────────────────────


class TestNormalizeDividends:
    def test_dates_become_datetime(self) -> None:
        payload = {
            "results": [{
                "ticker": "AAPL",
                "ex_dividend_date": "2024-08-12",
                "pay_date": "2024-08-15",
                "record_date": "2024-08-13",
                "declaration_date": "2024-08-01",
                "cash_amount": 0.25,
            }],
        }
        df = corporate_actions.normalize_dividends(payload)
        assert pd.api.types.is_datetime64_any_dtype(df["ex_dividend_date"])
        assert df.iloc[0]["cash_amount"] == 0.25

    def test_empty_results_returns_empty(self) -> None:
        df = corporate_actions.normalize_dividends({"results": []})
        assert df.empty


class TestNormalizeSplits:
    def test_execution_date_parsed(self) -> None:
        payload = {
            "results": [{
                "ticker": "AAPL",
                "execution_date": "2020-08-31",
                "split_from": 1,
                "split_to": 4,
            }],
        }
        df = corporate_actions.normalize_splits(payload)
        assert pd.api.types.is_datetime64_any_dtype(df["execution_date"])
        assert df.iloc[0]["split_to"] == 4

    def test_empty_results_returns_empty(self) -> None:
        df = corporate_actions.normalize_splits({"results": []})
        assert df.empty
