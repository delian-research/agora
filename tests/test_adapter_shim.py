"""Tests for the deprecated `agora.adapters.market` shim.

The shim translates legacy kwargs (``adjust`` → ``adjusted``,
``ohlcv=True`` → ``fields=(...)``) and forwards to
:func:`agora.equities.market.get_daily_prices` /
:func:`agora.equities.market.get_daily_returns` with ``source="rest"``.
Each call must emit ``DeprecationWarning``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from agora.adapters import market as adapter_market


def _fake_agg(date_ms: int, close: float) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=date_ms,
        open=close - 1, high=close + 1, low=close - 2, close=close,
        volume=1_000_000, transactions=10_000, vwap=close,
    )


def _client_returning(rows: list[SimpleNamespace]) -> MagicMock:
    fake = MagicMock()
    fake.rest.get_aggregates.return_value = rows
    return fake


# ── Deprecation warning ─────────────────────────────────────────────


class TestEmitsDeprecationWarning:
    def test_get_prices_warns(self) -> None:
        fake = _client_returning([_fake_agg(1735689600_000, 100.0)])
        with pytest.warns(DeprecationWarning, match="get_prices is deprecated"):
            adapter_market.get_prices(
                ["AAPL"], start="2025-01-01", end="2025-01-02", client=fake,
            )

    def test_get_returns_warns(self) -> None:
        fake = _client_returning([
            _fake_agg(1735689600_000, 100.0),
            _fake_agg(1735776000_000, 110.0),
        ])
        with pytest.warns(DeprecationWarning, match="get_returns is deprecated"):
            adapter_market.get_returns(
                ["AAPL"], start="2025-01-01", end="2025-01-02", client=fake,
            )


# ── Kwarg translation ──────────────────────────────────────────────


class TestKwargTranslation:
    def test_adjust_translates_to_adjusted(self) -> None:
        """The adapter's ``adjust=False`` should become ``adjusted=False``."""
        fake = _client_returning([_fake_agg(1735689600_000, 100.0)])

        with pytest.warns(DeprecationWarning):
            adapter_market.get_prices(
                ["AAPL"], start="2025-01-01", end="2025-01-02",
                adjust=False, client=fake,
            )

        # Inspect the kwarg actually passed to the underlying REST call.
        call_kwargs = fake.rest.get_aggregates.call_args.kwargs
        assert call_kwargs.get("adjusted") is False

    def test_ohlcv_true_returns_multi_field_columns(self) -> None:
        fake = _client_returning([_fake_agg(1735689600_000, 100.0)])

        with pytest.warns(DeprecationWarning):
            df = adapter_market.get_prices(
                ["AAPL"], start="2025-01-01", end="2025-01-02",
                ohlcv=True, client=fake,
            )

        # ohlcv=True maps to a multi-field equities call. Equities returns
        # MultiIndex (field, ticker) columns; verify all five fields land.
        assert isinstance(df.columns, pd.MultiIndex)
        fields = {field for (field, _) in df.columns}
        assert {"open", "high", "low", "close", "volume"} <= fields

    def test_ohlcv_false_returns_close_only(self) -> None:
        fake = _client_returning([_fake_agg(1735689600_000, 100.0)])

        with pytest.warns(DeprecationWarning):
            df = adapter_market.get_prices(
                ["AAPL"], start="2025-01-01", end="2025-01-02",
                ohlcv=False, client=fake,
            )

        # Default path: flat single-field DataFrame, columns are tickers.
        assert "AAPL" in df.columns
        assert not isinstance(df.columns, pd.MultiIndex)


# ── Behavior parity with equities ──────────────────────────────────


class TestParityWithEquities:
    """Spot-check that the shim produces equivalent close-price data
    to a direct equities call with ``source="rest"``."""

    def test_close_price_parity(self) -> None:
        # Same fake agg returned to both paths.
        fake1 = _client_returning([_fake_agg(1735689600_000, 100.0)])
        fake2 = _client_returning([_fake_agg(1735689600_000, 100.0)])

        with pytest.warns(DeprecationWarning):
            via_shim = adapter_market.get_prices(
                ["AAPL"], start="2025-01-01", end="2025-01-02", client=fake1,
            )

        from agora import equities
        via_equities = equities.get_daily_prices(
            ["AAPL"], start="2025-01-01", end="2025-01-02",
            source="rest", client=fake2,
        )

        pd.testing.assert_frame_equal(via_shim, via_equities)
