"""agora — Market data ingestion and local Parquet storage for Massive.com data.

Public API (everything below is importable as ``from agora import X``):

    Asset-class facades (recommended)
    ---------------------------------
    - :mod:`agora.equities` — domain-flavored helpers for equity prices,
      returns, volume, dividends, splits, snapshots. See ``dovs/d.equities.md``.

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

    Bulk download
    -------------
    - :func:`download_stocks`, :func:`download_forex`,
      :func:`download_reference`, :func:`download_ticker_events`

    Adapters (deprecated — use ``agora.equities`` instead)
    ------------------------------------------------------
    - :func:`get_prices`, :func:`get_returns` — thin shims that emit
      ``DeprecationWarning`` and forward to ``agora.equities`` calls.

    Errors
    ------
    - :class:`MassiveAPIError` and subclasses

Examples:
    >>> from agora import equities, MassiveClient, FlatFileLoader
    >>>
    >>> # Recommended path: agora.equities
    >>> prices  = equities.get_daily_prices(["AAPL", "MSFT"], period="1y")
    >>> returns = equities.get_daily_returns(["SPY"], period="2y", method="log")
    >>> snap    = equities.get_snapshot(["AAPL", "MSFT", "NVDA"])
    >>>
    >>> # Lower-level: live REST + offline Parquet via one client
    >>> with MassiveClient.from_env() as c:
    ...     aggs   = c.rest.get_aggregates("AAPL", 1, "day", "2024-01-01", "2024-12-31")
    ...     parquet = c.flat_files().get_stock_daily(["AAPL", "MSFT"], start="2024-01-01")
"""

__version__ = "0.1.0"

from agora import equities
from agora.adapters import get_prices, get_returns
from agora.client import MassiveClient, get_client, reset_client
from agora.config import MassiveConfig
from agora.download import (
    download_forex,
    download_reference,
    download_stocks,
    download_ticker_events,
)
from agora.errors import (
    MassiveAPIError,
    MassiveAuthenticationError,
    MassiveDataNotFoundError,
    MassiveRateLimitError,
)
from agora.loaders import (
    FlatFileLoader,
    MassiveDataApi,
    WebSocketStreamer,
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
