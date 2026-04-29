"""Data access loaders.

Three retrieval modes, each with a single class entry point:

- :class:`MassiveDataApi` — live REST API with retry/backoff
- :class:`FlatFileLoader` — read-only access to the local Parquet store
- :class:`WebSocketStreamer` — live trades / quotes / aggregates streaming

The deprecation shim at ``agora.loaders.s3`` is intentionally NOT
re-exported here; importing from it warns and points users to
``agora.loaders.parquet``.
"""

from agora.loaders.parquet import FlatFileLoader
from agora.loaders.rest import MassiveDataApi
from agora.loaders.socket import WebSocketStreamer

__all__ = [
    "FlatFileLoader",
    "MassiveDataApi",
    "WebSocketStreamer",
]
