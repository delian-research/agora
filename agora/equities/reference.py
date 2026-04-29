"""Static / point-in-time equity reference data.

Public functions (all stubs in v1 — implemented in a follow-up PR):
    - :func:`get_exchange`        primary listing exchange MIC
    - :func:`get_currency`         trading currency
    - :func:`get_country`          home country (REST `home_country` field)
    - :func:`get_market_cap`       current market cap (live REST)
    - :func:`get_shares_out`       current shares outstanding (live REST)

Future:
    - Historical shares outstanding — requires a new download step that
      snapshots `share_class_shares_outstanding` daily and writes to
      ``data/reference/shares_history.parquet``. Not in scope for v1.
    - ``get_region()`` mapping country → region (US/EU/APAC). Add when
      a real use case appears.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd


def _not_yet(name: str):
    raise NotImplementedError(
        f"agora.equities.reference.{name}() is scaffolded but not yet "
        "implemented. Track progress in 2.Projects.md."
    )


def get_exchange(tickers: str | Sequence[str]) -> pd.Series:
    """Primary listing exchange MIC code per ticker.

    Source: ``data/reference/tickers.parquet`` (no API call).
    """
    _not_yet("get_exchange")


def get_currency(tickers: str | Sequence[str]) -> pd.Series:
    """Trading currency per ticker (e.g., "usd").

    Source: ``data/reference/tickers.parquet``.
    """
    _not_yet("get_currency")


def get_country(tickers: str | Sequence[str]) -> pd.Series:
    """Home country per ticker (REST ``home_country`` field).

    Source: live REST ``get_ticker_details`` per ticker. Cache locally
    once the basket is stable.
    """
    _not_yet("get_country")


def get_market_cap(tickers: str | Sequence[str]) -> pd.Series:
    """Current market cap per ticker (live REST).

    Source: live REST ``get_ticker_details``. Returns the
    ``market_cap`` field as float.

    Note: only current market cap is available. Historical market cap
    requires shares-outstanding history which we don't yet ingest.
    """
    _not_yet("get_market_cap")


def get_shares_out(tickers: str | Sequence[str]) -> pd.Series:
    """Current shares outstanding per ticker (live REST).

    Source: live REST ``get_ticker_details`` ``share_class_shares_outstanding``
    field. Historical shares outstanding is a future feature.
    """
    _not_yet("get_shares_out")
