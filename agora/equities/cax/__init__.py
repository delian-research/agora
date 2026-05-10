"""Corporate actions for equities — dividends, splits, and adjustment factors.

Public functions:
    - :func:`get_dividends` (stub)
    - :func:`get_splits` (stub)

Both will read from local Parquet (``data/reference/{dividends,splits}.parquet``)
populated by ``agora-download reference``. Stubbed in v1; implemented next PR.
"""

from agora.equities.cax.dividends import get_dividends
from agora.equities.cax.splits import get_splits

__all__ = ["get_dividends", "get_splits"]
