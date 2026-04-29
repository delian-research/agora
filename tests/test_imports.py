"""Smoke tests for the public import surface.

These would have caught the BUG-1..11 audit findings *immediately*: they
verify that every module in the public surface is importable without
side-effects beyond loading ``.env``. Don't replace these with deeper
tests — they're cheap, fast, and cover a common failure mode (renamed
modules, missing identifiers, broken cross-imports) at zero maintenance
cost.
"""

from __future__ import annotations

import importlib
import warnings

import pytest


PUBLIC_MODULES = [
    "agora",
    "agora.config",
    "agora.client",
    "agora.errors",
    "agora.loaders",                      # subpackage facade
    "agora.loaders.rest",
    "agora.loaders.parquet",
    "agora.loaders.socket",
    "agora.adapters",                     # subpackage facade
    "agora.adapters.market",
    "agora.normalize",
    "agora.normalize.base",
    "agora.normalize.ohlc",
    "agora.normalize.snapshot",
    "agora.normalize.corporate_actions",
    "agora.download",
    "agora.download.stocks",
    "agora.download.forex",
    "agora.download.reference",
]


@pytest.mark.parametrize("name", PUBLIC_MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(name)


def test_version_exposed() -> None:
    import agora

    assert isinstance(agora.__version__, str)
    assert agora.__version__.count(".") == 2  # x.y.z


def test_top_level_public_surface() -> None:
    """The package facade — these names MUST be importable as `from agora import X`.

    This is the public contract. Adding new names is fine; removing or
    renaming any of these without a migration period would break callers.
    """
    import agora

    expected = {
        # Client / Config
        "MassiveClient", "MassiveConfig", "get_client", "reset_client",
        # Loaders
        "FlatFileLoader", "MassiveDataApi", "WebSocketStreamer",
        # Adapters
        "get_prices", "get_returns",
        # Bulk download
        "download_stocks", "download_forex",
        "download_reference", "download_ticker_events",
        # Errors
        "MassiveAPIError", "MassiveAuthenticationError",
        "MassiveDataNotFoundError", "MassiveRateLimitError",
    }
    missing = expected - set(dir(agora))
    assert not missing, f"top-level public names missing: {sorted(missing)}"


def test_top_level_all_matches_actual_exports() -> None:
    """`__all__` should be a subset of what's actually accessible."""
    import agora

    declared = set(agora.__all__)
    accessible = set(dir(agora))
    leaked = declared - accessible
    assert not leaked, f"declared in __all__ but missing: {sorted(leaked)}"


def test_subpackage_facades() -> None:
    """`agora.loaders` and `agora.adapters` should also re-export their classes."""
    from agora.loaders import FlatFileLoader, MassiveDataApi, WebSocketStreamer
    from agora.adapters import get_prices, get_returns

    assert FlatFileLoader.__name__ == "FlatFileLoader"
    assert MassiveDataApi.__name__ == "MassiveDataApi"
    assert WebSocketStreamer.__name__ == "WebSocketStreamer"
    assert callable(get_prices)
    assert callable(get_returns)


def test_download_public_exports() -> None:
    """Regression check for BUG-10 (download_ticker_events not exported)."""
    from agora.download import (
        download_forex,
        download_reference,
        download_stocks,
        download_ticker_events,
    )

    assert callable(download_stocks)
    assert callable(download_forex)
    assert callable(download_reference)
    assert callable(download_ticker_events)


def test_s3_shim_warns_and_aliases() -> None:
    """``agora.loaders.s3`` should keep working but emit a DeprecationWarning."""
    # Import fresh so the warning fires (other tests may have already
    # triggered it; force a re-import by deleting from sys.modules).
    import sys

    sys.modules.pop("agora.loaders.s3", None)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        from agora.loaders.s3 import FlatFileLoader as Shimmed
        from agora.loaders.parquet import FlatFileLoader as Canonical

    deps = [w for w in captured if issubclass(w.category, DeprecationWarning)]
    assert len(deps) == 1
    assert "agora.loaders.parquet" in str(deps[0].message)
    assert Shimmed is Canonical


def test_massive_client_factory() -> None:
    """Regression check for BUGs 4–7 (MassiveClient.from_env was broken)."""
    from agora.client import MassiveClient
    from agora.loaders.parquet import FlatFileLoader
    from agora.loaders.rest import MassiveDataApi
    from agora.loaders.socket import WebSocketStreamer

    c = MassiveClient.from_env()
    assert isinstance(c.rest, MassiveDataApi)
    assert isinstance(c.flat_files(), FlatFileLoader)
    assert isinstance(c.ws_streamer(market="stocks"), WebSocketStreamer)
