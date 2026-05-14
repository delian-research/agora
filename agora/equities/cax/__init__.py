"""Corporate actions for equities via the Massive REST API.

Public functions:
    - :func:`get_dividends` — dividend events filtered by ticker/date range
    - :func:`get_splits` — split events filtered by ticker/date range

These helpers are API-first and do not read from the local Parquet store.
Use :class:`agora.loaders.parquet.FlatFileLoader` for offline access to
``data/reference/{dividends,splits}.parquet``.
"""

from agora.equities.cax.dividends import get_dividends
from agora.equities.cax.splits import get_splits

__all__ = ["get_dividends", "get_splits"]
