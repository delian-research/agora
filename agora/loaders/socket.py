"""WebSocket streaming loader for Massive real-time / delayed market data.

This module wraps the official ``massive.WebSocketClient`` with:

- Env-based authentication (loaded via ``MassiveConfig``)
- Per-market channel routing — high-level verbs (``subscribe_trades`` /
  ``subscribe_minute_aggs`` / etc.) automatically map to the right channel
  code for stocks, forex, crypto, or indices
- A handler registry: register multiple callbacks, optionally filtered by
  event type
- Optional message buffer and JSONL sink for capture / replay
- Optional ``timeout`` on ``run()`` for deterministic test harnesses
- Context-manager support and clean ``close()``

The Massive WS API is per-market: each ``WebSocketStreamer`` instance
connects to a single market (e.g. ``stocks``). Subscriptions look like
``{event_code}.{symbol}`` where the event code depends on both the market
and the channel (T, Q, A, AM for stocks; C, CA, CAS for forex; XT, XQ, XA,
XAS for crypto; V for indices). This wrapper hides those codes.

Plan note: real-time stocks WS requires a paid feed entitlement. Stocks
Starter typically only includes the **delayed** feed (``delayed.massive.com``,
~15-min delayed), which is the default here. Pass ``feed=Feed.RealTime``
explicitly if your subscription supports it.

Examples:
    Stream live trades & minute bars for a basket::

        from agora.loaders.socket import WebSocketStreamer

        streamer = WebSocketStreamer(market="stocks")
        streamer.subscribe_trades("AAPL", "MSFT")
        streamer.subscribe_minute_aggs("AAPL", "MSFT")

        @streamer.on_message
        def handle(msg):
            print(msg.event_type, getattr(msg, "symbol", None), msg)

        streamer.run()  # Ctrl+C to stop

    Capture forex quotes for 30 seconds into a list::

        with WebSocketStreamer(market="forex") as s:
            s.subscribe_quotes("C:EURUSD", "C:GBPUSD")
            s.buffer(maxlen=10000)
            s.run(timeout=30)
            print(f"Captured {len(s.buffered)} quotes")

    Stream live data to a JSONL file::

        with WebSocketStreamer(market="stocks") as s:
            s.subscribe_minute_aggs("*")  # all minute bars
            s.dump_to_jsonl("data/live/agg_minute.jsonl")
            s.run()
"""

from __future__ import annotations

import _thread
import json
import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from massive import WebSocketClient
from massive.websocket.models.common import Feed, Market

from agora.config import MassiveConfig

logger = logging.getLogger(__name__)


# ── Channel code registry ───────────────────────────────────────────
# Maps (market, verb) -> Massive event code used in subscription strings.
# The official enum is `EventType` in massive.websocket.models; we keep
# this table so callers don't have to know the codes.

_CHANNEL_CODES: dict[str, dict[str, str]] = {
    "stocks": {
        "trades": "T",
        "quotes": "Q",
        "second_aggs": "A",
        "minute_aggs": "AM",
        "luld": "LULD",
        "imbalances": "NOI",
    },
    "forex": {
        "quotes": "C",
        "minute_aggs": "CA",
        "second_aggs": "CAS",
    },
    "crypto": {
        "trades": "XT",
        "quotes": "XQ",
        "minute_aggs": "XA",
        "second_aggs": "XAS",
        "l2_book": "XL2",
    },
    "indices": {
        "values": "V",
    },
}


HandlerFn = Callable[[Any], None]


@dataclass
class _Subscription:
    """Internal representation of a subscription string (channel + symbol)."""

    channel: str
    symbol: str

    def to_param(self) -> str:
        return f"{self.channel}.{self.symbol}"


class WebSocketStreamer:
    """High-level wrapper around ``massive.WebSocketClient``.

    Parameters
    ----------
    market : str
        One of ``"stocks"``, ``"forex"``, ``"crypto"``, ``"indices"``.
        Determines which channel codes apply to ``subscribe_*`` calls.
    feed : Feed | str, optional
        Massive feed (URL). Defaults to ``Feed.Delayed`` (free with paid
        plans). Use ``Feed.RealTime`` if your plan includes real-time WS.
    api_key : str, optional
        Override API key. Defaults to ``MASSIVE_API_KEY`` env var.
    config : MassiveConfig, optional
        Pre-built config to use instead of loading from env.
    max_reconnects : int, optional
        Number of automatic reconnect attempts (default 5).
    raw : bool, optional
        If True, the underlying client delivers raw bytes/strings to the
        handler instead of parsed message objects. Default False.
    """

    # Verbs that any market may expose. We declare them here so
    # type-checkers / IDE autocomplete see them. Each method body
    # delegates to ``_subscribe`` with the right verb.
    _VERBS = (
        "trades",
        "quotes",
        "minute_aggs",
        "second_aggs",
        "luld",
        "imbalances",
        "values",
        "l2_book",
    )

    def __init__(
        self,
        market: str | Market = "stocks",
        *,
        feed: str | Feed = Feed.Delayed,
        api_key: str | None = None,
        config: MassiveConfig | None = None,
        max_reconnects: int = 5,
        raw: bool = False,
    ):
        if config is None:
            config = MassiveConfig.from_env(api_key=api_key)

        self.config = config
        self.market = (
            market.value if isinstance(market, Market) else str(market).lower()
        )
        self.feed = feed.value if isinstance(feed, Feed) else feed

        if self.market not in _CHANNEL_CODES:
            raise ValueError(
                f"Unsupported market '{self.market}'. "
                f"Allowed: {sorted(_CHANNEL_CODES)}"
            )

        self._client = WebSocketClient(
            api_key=config.api_key,
            feed=self.feed,
            market=self.market,
            max_reconnects=max_reconnects,
            raw=raw,
        )

        self._handlers: list[tuple[set[str] | None, HandlerFn]] = []
        self._subs: set[str] = set()

        # Optional capture mechanisms
        self._buffer: deque | None = None
        self._jsonl_path: Path | None = None
        self._jsonl_fh: TextIO | None = None
        self._lock = threading.Lock()

    # ── Subscription helpers ────────────────────────────────────────

    def _channel_for(self, verb: str) -> str:
        codes = _CHANNEL_CODES[self.market]
        if verb not in codes:
            raise ValueError(
                f"Channel '{verb}' is not available for market '{self.market}'. "
                f"Allowed for this market: {sorted(codes)}"
            )
        return codes[verb]

    def _subscribe(self, verb: str, symbols: Iterable[str]) -> None:
        channel = self._channel_for(verb)
        params: list[str] = []
        for s in symbols:
            sym = self._normalize_symbol(s)
            sub = _Subscription(channel=channel, symbol=sym).to_param()
            self._subs.add(sub)
            params.append(sub)
        if params:
            self._client.subscribe(*params)
            logger.debug("subscribed: %s", params)

    def _normalize_symbol(self, sym: str) -> str:
        """Light symbol normalization for forex slash-notation convenience."""
        sym = sym.strip()
        if sym == "*":
            return "*"
        if self.market == "forex" and "/" in sym and not sym.startswith("C:"):
            base, quote = sym.split("/", 1)
            return f"C:{base.upper()}{quote.upper()}"
        if self.market == "crypto" and "/" in sym and not sym.startswith("X:"):
            base, quote = sym.split("/", 1)
            return f"X:{base.upper()}{quote.upper()}"
        return sym

    # Public verb-based subscribers. Each one validates the market
    # supports the channel via ``_channel_for``.

    def subscribe_trades(self, *symbols: str) -> None:
        """Subscribe to trades. Stocks: T.{ticker}; Crypto: XT.{pair}."""
        self._subscribe("trades", symbols)

    def subscribe_quotes(self, *symbols: str) -> None:
        """Subscribe to quotes. Stocks: Q; Forex: C; Crypto: XQ."""
        self._subscribe("quotes", symbols)

    def subscribe_minute_aggs(self, *symbols: str) -> None:
        """Subscribe to per-minute aggregate bars (AM / CA / XA)."""
        self._subscribe("minute_aggs", symbols)

    def subscribe_second_aggs(self, *symbols: str) -> None:
        """Subscribe to per-second aggregate bars (A / CAS / XAS)."""
        self._subscribe("second_aggs", symbols)

    def subscribe_values(self, *symbols: str) -> None:
        """Subscribe to index values (indices market only, V.{ticker})."""
        self._subscribe("values", symbols)

    def subscribe_luld(self, *symbols: str) -> None:
        """Subscribe to limit-up/limit-down events (stocks only)."""
        self._subscribe("luld", symbols)

    def subscribe_imbalances(self, *symbols: str) -> None:
        """Subscribe to imbalance messages (stocks only, NOI.{ticker})."""
        self._subscribe("imbalances", symbols)

    def subscribe_l2_book(self, *symbols: str) -> None:
        """Subscribe to crypto L2 order book (crypto only, XL2.{pair})."""
        self._subscribe("l2_book", symbols)

    def subscribe_raw(self, *params: str) -> None:
        """Escape hatch: pass raw subscription strings (e.g. ``T.AAPL``)."""
        for p in params:
            self._subs.add(p)
        if params:
            self._client.subscribe(*params)

    def unsubscribe(self, *params: str) -> None:
        """Unsubscribe specific subscription strings."""
        if not params:
            return
        self._client.unsubscribe(*params)
        for p in params:
            self._subs.discard(p)

    def unsubscribe_all(self) -> None:
        """Drop every active subscription."""
        self._client.unsubscribe_all()
        self._subs.clear()

    @property
    def subscriptions(self) -> set[str]:
        return set(self._subs)

    # ── Handler registration ────────────────────────────────────────

    def on_message(
        self,
        fn: HandlerFn | None = None,
        *,
        events: Iterable[str] | None = None,
    ) -> HandlerFn:
        """Register a handler. Usable as decorator or direct call.

        ``events`` filters by Massive event-type codes (e.g. ``["T", "AM"]``).
        If omitted, the handler receives every message.

        Examples::

            @streamer.on_message
            def handle_all(msg): ...

            @streamer.on_message(events=["T"])
            def handle_trades(msg): ...

            streamer.on_message(my_function)
        """
        event_set = set(events) if events else None

        def _register(handler: HandlerFn) -> HandlerFn:
            self._handlers.append((event_set, handler))
            return handler

        # Decorator-without-args form: on_message(fn)
        if fn is not None:
            return _register(fn)
        # Decorator-with-args form: on_message(events=[...]) returns a
        # decorator; the actual function is registered when applied.
        return _register  # type: ignore[return-value]

    # ── Capture helpers ─────────────────────────────────────────────

    def buffer(self, maxlen: int = 10000) -> None:
        """Enable an in-memory buffer of incoming messages.

        Access via ``streamer.buffered``. The buffer is a ``deque(maxlen=...)``
        so old messages are discarded once full.
        """
        self._buffer = deque(maxlen=maxlen)

    @property
    def buffered(self) -> list:
        return list(self._buffer or [])

    def dump_to_jsonl(self, path: str | Path) -> None:
        """Stream every received message to a JSONL file (one event per line)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Open in append mode so reconnects/resumes don't truncate prior data.
        self._jsonl_path = path
        self._jsonl_fh = path.open("a", buffering=1)  # line-buffered

    # ── Run / close ─────────────────────────────────────────────────

    def _dispatch(self, msgs: list) -> None:
        """Internal handler called by the SDK with each batch of messages."""
        # ``msgs`` is List[WebSocketMessage]. Each message has an
        # ``event_type`` attribute holding the channel code.
        for m in msgs:
            event_code = getattr(m, "event_type", None)
            event_code = getattr(event_code, "value", event_code)

            # Buffer + JSONL sink first so capture happens regardless of
            # whether handlers raise.
            with self._lock:
                if self._buffer is not None:
                    self._buffer.append(m)
                if self._jsonl_fh is not None:
                    self._jsonl_fh.write(_message_to_json(m) + "\n")

            for filter_set, handler in self._handlers:
                if filter_set is not None and event_code not in filter_set:
                    continue
                try:
                    handler(m)
                except Exception:
                    logger.exception("handler %s raised", handler)

    def run(self, timeout: float | None = None) -> None:
        """Connect and process messages (blocking).

        Parameters
        ----------
        timeout : float, optional
            If given, the run loop is interrupted after this many seconds via
            a ``KeyboardInterrupt`` raised on the main thread. The interrupt
            propagates through the SDK's ``asyncio.run`` and we catch it
            here for a clean shutdown.
        """
        if not self._handlers:
            logger.warning(
                "WebSocketStreamer.run() called with no handlers — messages "
                "will be received but only processed if buffer/jsonl is enabled."
            )

        timer: threading.Timer | None = None
        if timeout is not None:
            timer = threading.Timer(timeout, _thread.interrupt_main)
            timer.daemon = True
            timer.start()

        try:
            self._client.run(self._dispatch)
        except KeyboardInterrupt:
            logger.info("WebSocketStreamer.run interrupted (timeout or Ctrl+C)")
        finally:
            if timer is not None:
                timer.cancel()
            self._flush_sinks()

    def close(self) -> None:
        """Close the underlying connection and any open sinks.

        ``WebSocketClient.close`` is an async coroutine. After ``run()``
        returns, ``asyncio.run`` has already torn down the loop and the
        socket, so the coroutine returned here is effectively a no-op.
        We discard it explicitly to silence Python's "coroutine was never
        awaited" warning.
        """
        import inspect
        try:
            result = self._client.close()
            if inspect.iscoroutine(result):
                # Connection already torn down by asyncio.run(); just close
                # the coroutine object so Python doesn't warn about it.
                result.close()
        except Exception:
            logger.debug("close: client.close() raised", exc_info=True)
        self._flush_sinks()

    def _flush_sinks(self) -> None:
        if self._jsonl_fh is not None:
            try:
                self._jsonl_fh.flush()
                self._jsonl_fh.close()
            except Exception:
                logger.debug("flush_sinks: jsonl close raised", exc_info=True)
            finally:
                self._jsonl_fh = None

    def __enter__(self) -> WebSocketStreamer:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def _message_to_json(msg: Any) -> str:
    """Best-effort JSON serialization for a WebSocketMessage."""
    if isinstance(msg, (dict, list, str, int, float, bool)) or msg is None:
        return json.dumps(msg, default=str)
    # SDK message objects are ``modelclass``-decorated dataclasses with
    # ``__dict__``. Fall back to ``vars`` and finally to ``str``.
    try:
        return json.dumps(vars(msg), default=str)
    except TypeError:
        return json.dumps(str(msg))