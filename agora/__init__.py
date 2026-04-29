"""agora — Market data ingestion and local Parquet storage for Massive.com data.

Public API (everything below is importable as ``from agora import X``):

    Client / Config
    ---------------
    - :class:`MassiveClient` — orchestrator bundling REST / Parquet / WebSocket
    - :class:`MassiveConfig` — env-loaded configuration dataclass
    - :func:`get_client`, :func:`reset_client` — process-wide singleton helpers

    Loaders
    -------
    - :class:`MassiveDataApi` — live REST with retry/backoff
    - :class:`FlatFileLoader` — read-only local Parquet access
    - :class:`WebSocketStreamer` — live trades/quotes/aggregates streaming

    Adapters
    --------
    - :func:`get_prices` — pivoted OHLC matrix for a basket
    - :func:`get_returns` — daily returns for a basket

    Bulk download
    -------------
    - :func:`download_stocks`, :func:`download_forex`,
      :func:`download_reference`, :func:`download_ticker_events`

    Errors
    ------
    - :class:`MassiveAPIError` and subclasses

Examples:
    >>> from agora import MassiveClient, FlatFileLoader, get_prices
    >>>
    >>> # Live REST + offline Parquet via one client
    >>> with MassiveClient.from_env() as c:
    ...     aggs   = c.rest.get_aggregates("AAPL", 1, "day", "2024-01-01", "2024-12-31")
    ...     prices = c.flat_files().get_prices(["AAPL", "MSFT"], start="2024-01-01")
    >>>
    >>> # Or use loaders directly
    >>> loader = FlatFileLoader()
    >>> prices = loader.get_prices(["SPY"], start="2024-01-01")
    >>>
    >>> # Or use the high-level adapter
    >>> prices = get_prices(["AAPL", "MSFT"], period="1y")
"""

__version__ = "0.1.0"

# ── Client / Config ─────────────────────────────────────────────────
from agora.client import MassiveClient, get_client, reset_client
from agora.config import MassiveConfig

# ── Errors ──────────────────────────────────────────────────────────
from agora.errors import (
    MassiveAPIError,
    MassiveAuthenticationError,
    MassiveDataNotFoundError,
    MassiveRateLimitError,
)

# ── Loaders (live + offline + streaming) ────────────────────────────
from agora.loaders import (
    FlatFileLoader,
    MassiveDataApi,
    WebSocketStreamer,
)

# ── Asset-class facades ─────────────────────────────────────────────
from agora import equities

# ── Analytics adapters ──────────────────────────────────────────────
from agora.adapters import get_prices, get_returns

# ── Bulk download ───────────────────────────────────────────────────
from agora.download import (
    download_forex,
    download_reference,
    download_stocks,
    download_ticker_events,
)

__all__ = [
    "__version__",
    # Client / Config
    "MassiveClient",
    "MassiveConfig",
    "get_client",
    "reset_client",
    # Loaders
    "FlatFileLoader",
    "MassiveDataApi",
    "WebSocketStreamer",
    # Asset-class facades
    "equities",
    # Adapters
    "get_prices",
    "get_returns",
    # Bulk download
    "download_forex",
    "download_reference",
    "download_stocks",
    "download_ticker_events",
    # Errors
    "MassiveAPIError",
    "MassiveAuthenticationError",
    "MassiveDataNotFoundError",
    "MassiveRateLimitError",
]
