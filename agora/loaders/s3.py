"""Deprecated shim — use ``agora.loaders.parquet`` instead.

This module exists only so older imports of ``agora.loaders.s3`` keep
working. The loader was renamed because it reads **local Parquet files**,
not S3 — the S3 client lives in ``agora/download/config.py``.

The shim emits a ``DeprecationWarning`` on import and re-exports the
public surface unchanged, so callers can migrate at their own pace::

    # Old (still works, warns)
    from agora.loaders.s3 import FlatFileLoader

    # New (preferred)
    from agora.loaders.parquet import FlatFileLoader
"""

from __future__ import annotations

import warnings

warnings.warn(
    "agora.loaders.s3 is deprecated and will be removed in a future "
    "release. Import from agora.loaders.parquet instead "
    "(the loader reads local Parquet, not S3).",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export the entire public surface. Using `from … import *` style
# explicitly to keep IDE autocomplete and `from agora.loaders.s3 import X`
# working for every name.
from agora.loaders.parquet import *  # noqa: E402,F401,F403
from agora.loaders.parquet import FlatFileLoader  # noqa: E402,F401  (explicit re-export)