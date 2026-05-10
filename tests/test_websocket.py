"""Structural tests for ``WebSocketStreamer``.

These exercise channel-code routing, handler dispatch, capture
mechanisms, and validation, all without opening a real WebSocket — the
``WebSocketClient.subscribe`` call is mocked so we never hit Massive's
servers from CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agora.loaders.socket import WebSocketStreamer

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def streamer():
    s = WebSocketStreamer(market="stocks")
    s._client.subscribe = MagicMock()  # don't open a real socket
    return s


class FakeMsg:
    """Stand-in for the SDK's WebSocketMessage."""

    def __init__(self, event_type: str, **extra):
        self.event_type = event_type
        for k, v in extra.items():
            setattr(self, k, v)

    def __repr__(self):
        return f"<{self.event_type}>"


# ── Construction ────────────────────────────────────────────────────


def test_default_market_and_feed():
    s = WebSocketStreamer(market="stocks")
    assert s.market == "stocks"
    assert s.feed.endswith("massive.com")


def test_invalid_market_raises():
    with pytest.raises(ValueError, match="Unsupported market"):
        WebSocketStreamer(market="foo")


# ── Channel-code routing ────────────────────────────────────────────


def test_stocks_verbs_route_to_stocks_codes(streamer):
    streamer.subscribe_trades("AAPL")
    streamer.subscribe_quotes("MSFT")
    streamer.subscribe_minute_aggs("SPY")
    streamer.subscribe_second_aggs("NVDA")

    assert streamer.subscriptions == {"T.AAPL", "Q.MSFT", "AM.SPY", "A.NVDA"}


def test_forex_routes_to_forex_codes_with_slash_normalization():
    s = WebSocketStreamer(market="forex")
    s._client.subscribe = MagicMock()

    s.subscribe_quotes("EUR/USD", "C:GBPUSD")
    s.subscribe_minute_aggs("C:JPYUSD")

    assert s.subscriptions == {"C.C:EURUSD", "C.C:GBPUSD", "CA.C:JPYUSD"}


def test_indices_only_supports_values():
    s = WebSocketStreamer(market="indices")
    s._client.subscribe = MagicMock()

    s.subscribe_values("I:SPX")
    assert "V.I:SPX" in s.subscriptions

    with pytest.raises(ValueError, match="not available for market 'indices'"):
        s.subscribe_trades("I:SPX")


def test_crypto_routes_to_x_prefixed_codes():
    s = WebSocketStreamer(market="crypto")
    s._client.subscribe = MagicMock()

    s.subscribe_trades("X:BTCUSD")
    s.subscribe_quotes("X:ETHUSD")
    s.subscribe_minute_aggs("BTC/USD")  # slash normalization

    assert s.subscriptions == {"XT.X:BTCUSD", "XQ.X:ETHUSD", "XA.X:BTCUSD"}


def test_subscribe_raw_escape_hatch(streamer):
    streamer.subscribe_raw("NOI.AAPL", "LULD.AAPL")
    assert {"NOI.AAPL", "LULD.AAPL"} <= streamer.subscriptions


# ── Handler dispatch ────────────────────────────────────────────────


def test_dispatch_routes_to_filtered_and_unfiltered_handlers(streamer):
    counts = {"all": 0, "trade": 0, "quote": 0}

    @streamer.on_message
    def _all(_msg):
        counts["all"] += 1

    @streamer.on_message(events=["T"])
    def _trade(_msg):
        counts["trade"] += 1

    streamer.on_message(lambda _m: counts.__setitem__("quote", counts["quote"] + 1),
                        events=["Q"])

    streamer._dispatch([FakeMsg("T"), FakeMsg("T"), FakeMsg("Q"), FakeMsg("AM")])

    assert counts == {"all": 4, "trade": 2, "quote": 1}


def test_handler_exceptions_are_logged_not_propagated(streamer):
    """A bad handler must not stop later handlers from running."""
    seen = []

    @streamer.on_message
    def bad(_msg):
        raise RuntimeError("bad handler")

    @streamer.on_message
    def good(msg):
        seen.append(msg.event_type)

    streamer._dispatch([FakeMsg("T")])
    assert seen == ["T"]


# ── Capture helpers ─────────────────────────────────────────────────


def test_buffer_captures_messages(streamer):
    streamer.buffer(maxlen=100)
    streamer._dispatch([FakeMsg("T"), FakeMsg("Q"), FakeMsg("AM")])
    assert len(streamer.buffered) == 3


def test_buffer_respects_maxlen(streamer):
    streamer.buffer(maxlen=2)
    streamer._dispatch([FakeMsg("T"), FakeMsg("Q"), FakeMsg("AM")])
    assert len(streamer.buffered) == 2


def test_jsonl_sink_writes_one_event_per_line(tmp_path: Path, streamer):
    out = tmp_path / "events.jsonl"
    streamer.dump_to_jsonl(out)

    streamer._dispatch([FakeMsg("T", symbol="AAPL"), FakeMsg("Q", symbol="MSFT")])
    streamer._flush_sinks()

    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event_type"] == "T"
    assert parsed[0]["symbol"] == "AAPL"
