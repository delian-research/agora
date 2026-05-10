"""High-level orchestrator client.

``MassiveClient`` holds a single ``MassiveConfig`` and lazy-instantiates
the various loader sub-clients (REST, WebSocket, FlatFile) so callers
don't have to wire them by hand.

Examples:
    >>> from agora.client import MassiveClient
    >>> with MassiveClient.from_env() as c:
    ...     aggs = c.rest.get_aggregates("AAPL", 1, "day", "2024-01-01", "2024-12-31")
"""

from __future__ import annotations

import logging

from .config import MassiveConfig
from .loaders.rest import MassiveDataApi

logger = logging.getLogger(__name__)


class MassiveClient:
    """Orchestrator that bundles config + loader clients.

    Attributes:
        config: ``MassiveConfig`` used by every sub-client.
        rest:   ``MassiveDataApi`` with retry/backoff around the SDK.
    """

    def __init__(self, config: MassiveConfig) -> None:
        self.config = config
        self.rest: MassiveDataApi = MassiveDataApi(config)

    @classmethod
    def from_env(cls, *, api_key: str | None = None) -> MassiveClient:
        """Build a client with config loaded from the environment.

        Args:
            api_key: Optional override; otherwise read from MASSIVE_API_KEY.
        """
        config = MassiveConfig.from_env(api_key=api_key)
        return cls(config)

    def flat_files(self, data_dir=None):
        """Return a ``FlatFileLoader`` bound to the local Parquet data store.

        Imported lazily so users who don't need the local-file loader don't
        pay the pandas/pyarrow import cost up-front.
        """
        from .loaders.parquet import FlatFileLoader

        return FlatFileLoader(data_dir=data_dir)

    def ws_streamer(self, market: str = "stocks", **kwargs):
        """Return a ``WebSocketStreamer`` bound to this client's API key.

        Args:
            market: ``"stocks"`` | ``"forex"`` | ``"crypto"`` | ``"indices"``.
            **kwargs: Forwarded to ``WebSocketStreamer`` (e.g. ``feed=...``).
        """
        from .loaders.socket import WebSocketStreamer

        return WebSocketStreamer(market=market, config=self.config, **kwargs)

    def close(self) -> None:
        """Release any resources held by sub-clients."""
        try:
            self.rest.close()
        except Exception:
            logger.debug("close: rest.close() raised", exc_info=True)

    def __enter__(self) -> MassiveClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ── Module-level singleton ──────────────────────────────────────────

_client: MassiveClient | None = None


def get_client() -> MassiveClient:
    """Return a process-wide ``MassiveClient`` (created lazily).

    Examples:
        >>> client = get_client()
        >>> aggs = client.rest.get_aggregates(
        ...     "AAPL", 1, "day", "2024-01-01", "2024-12-31"
        ... )
    """
    global _client
    if _client is None:
        _client = MassiveClient.from_env()
    return _client


def reset_client() -> None:
    """Drop the cached singleton (next ``get_client()`` will rebuild it)."""
    global _client
    if _client is not None:
        _client.close()
    _client = None