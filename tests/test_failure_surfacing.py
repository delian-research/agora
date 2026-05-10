"""Tests for partial-failure surfacing in price/snapshot fetch paths.

When a per-ticker REST call fails:
    - ``strict=True`` propagates the first error.
    - ``strict=False`` (default) logs a warning, skips the failing ticker,
      and exposes the failed list via ``df.attrs["failed_tickers"]``.

These tests are pure-mock — no network, no data/.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from agora import equities
from agora.adapters import market as adapter_market
from agora.errors import MassiveAPIError


def _fake_agg(date_ms: int, close: float) -> SimpleNamespace:
    """Build a SimpleNamespace mimicking a Massive aggregate."""
    return SimpleNamespace(
        timestamp=date_ms,
        open=close - 1, high=close + 1, low=close - 2, close=close,
        volume=1_000_000, transactions=10_000, vwap=close,
    )


def _client_with_aggregates_side_effect(side_effect_map: dict[str, object]) -> MagicMock:
    """Build a fake client whose ``rest.get_aggregates(ticker, ...)`` honors
    a per-ticker side-effect map: either a list of agg objects or an Exception.
    """
    fake = MagicMock()

    def _get_aggregates(ticker: str, **_):
        result = side_effect_map[ticker]
        if isinstance(result, Exception):
            raise result
        return result

    fake.rest.get_aggregates.side_effect = _get_aggregates
    return fake


# ── equities.get_daily_prices (REST source) ─────────────────────────


class TestEquitiesPricesPartialFailure:
    def test_strict_false_returns_survivors_and_populates_attrs(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # AAPL succeeds; BADD raises; MSFT succeeds.
        fake = _client_with_aggregates_side_effect({
            "AAPL": [_fake_agg(1735689600_000, 100.0)],
            "BADD": MassiveAPIError("simulated failure"),
            "MSFT": [_fake_agg(1735689600_000, 200.0)],
        })

        with caplog.at_level(logging.WARNING, logger="agora.equities.market"):
            df = equities.get_daily_prices(
                ["AAPL", "BADD", "MSFT"],
                start="2025-01-01", end="2025-01-02",
                source="rest",
                client=fake,
            )

        # Survivors come back with the right shape.
        assert "AAPL" in df.columns
        assert "MSFT" in df.columns
        assert "BADD" not in df.columns
        # Failed list is exposed.
        assert df.attrs.get("failed_tickers") == ["BADD"]
        # Warning was logged with structured extra.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(getattr(r, "ticker", None) == "BADD" for r in warnings)

    def test_strict_true_raises_on_first_failure(self) -> None:
        fake = _client_with_aggregates_side_effect({
            "AAPL": [_fake_agg(1735689600_000, 100.0)],
            "BADD": MassiveAPIError("simulated failure"),
        })

        with pytest.raises(MassiveAPIError):
            equities.get_daily_prices(
                ["AAPL", "BADD"],
                start="2025-01-01", end="2025-01-02",
                source="rest",
                client=fake,
                strict=True,
            )

    def test_all_failures_returns_empty_with_attrs(self) -> None:
        fake = _client_with_aggregates_side_effect({
            "BAD1": MassiveAPIError("nope"),
            "BAD2": MassiveAPIError("nope"),
        })

        df = equities.get_daily_prices(
            ["BAD1", "BAD2"],
            start="2025-01-01", end="2025-01-02",
            source="rest",
            client=fake,
        )

        assert df.empty
        assert df.attrs.get("failed_tickers") == ["BAD1", "BAD2"]


class TestEquitiesReturnsPartialFailure:
    def test_returns_propagates_failed_tickers_attrs(self) -> None:
        # Two days of data so pct_change yields one valid row.
        fake = _client_with_aggregates_side_effect({
            "AAPL": [
                _fake_agg(1735689600_000, 100.0),
                _fake_agg(1735776000_000, 110.0),
            ],
            "BADD": MassiveAPIError("simulated failure"),
        })

        df = equities.get_daily_returns(
            ["AAPL", "BADD"],
            start="2025-01-01", end="2025-01-02",
            source="rest",
            client=fake,
        )

        assert "AAPL" in df.columns
        assert df.attrs.get("failed_tickers") == ["BADD"]


# ── equities.get_snapshot single-ticker fast path ────────────────────


class TestEquitiesSnapshotPartialFailure:
    def test_strict_false_returns_empty_with_attrs_on_single_ticker_failure(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.side_effect = MassiveAPIError("simulated failure")

        with caplog.at_level(logging.WARNING, logger="agora.equities.market"):
            df = equities.get_snapshot("BADD", client=fake)

        assert df.empty
        assert df.attrs.get("failed_tickers") == ["BADD"]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(getattr(r, "ticker", None) == "BADD" for r in warnings)

    def test_strict_true_raises_on_single_ticker_failure(self) -> None:
        fake = MagicMock()
        fake.rest.get_snapshot.side_effect = MassiveAPIError("simulated failure")

        with pytest.raises(MassiveAPIError):
            equities.get_snapshot("BADD", client=fake, strict=True)


# ── adapters.get_prices (legacy facade) ─────────────────────────────


class TestAdapterPricesPartialFailure:
    def test_strict_false_returns_survivors_and_populates_attrs(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        fake = _client_with_aggregates_side_effect({
            "AAPL": [_fake_agg(1735689600_000, 100.0)],
            "BADD": MassiveAPIError("simulated failure"),
        })

        with caplog.at_level(logging.WARNING, logger="agora.adapters.market"):
            df = adapter_market.get_prices(
                ["AAPL", "BADD"],
                start="2025-01-01", end="2025-01-02",
                client=fake,
            )

        assert "AAPL" in df.columns
        assert "BADD" not in df.columns
        assert df.attrs.get("failed_tickers") == ["BADD"]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(getattr(r, "ticker", None) == "BADD" for r in warnings)

    def test_strict_true_raises_on_first_failure(self) -> None:
        fake = _client_with_aggregates_side_effect({
            "BADD": MassiveAPIError("simulated failure"),
        })

        with pytest.raises(MassiveAPIError):
            adapter_market.get_prices(
                ["BADD"],
                start="2025-01-01", end="2025-01-02",
                strict=True,
                client=fake,
            )

    def test_all_failures_returns_empty_with_attrs(self) -> None:
        fake = _client_with_aggregates_side_effect({
            "BAD1": MassiveAPIError("nope"),
            "BAD2": MassiveAPIError("nope"),
        })

        df = adapter_market.get_prices(
            ["BAD1", "BAD2"],
            start="2025-01-01", end="2025-01-02",
            client=fake,
        )

        assert df.empty
        assert df.attrs.get("failed_tickers") == ["BAD1", "BAD2"]


# ── Sanity: parquet path is unaffected ──────────────────────────────


class TestParquetPathDoesNotAddAttrs:
    def test_parquet_source_no_attrs_when_no_failures(self) -> None:
        # Build a tiny synthetic DataFrame and inject via patching.
        # We don't have data/, so use a tmp dir.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            stocks_dir = pd.Index([])  # placeholder
            del stocks_dir
            # Easier: just call with empty parquet store and confirm we
            # get an empty df without failed_tickers attr.
            df = equities.get_daily_prices(
                ["AAPL"],
                start="2099-01-01", end="2099-01-02",  # future range
                source="parquet",
                data_dir=td,
            )
            assert df.empty
            assert "failed_tickers" not in df.attrs
