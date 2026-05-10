"""Industry / sector classification from SIC codes.

Source: live REST ``get_ticker_details`` returns ``sic_code`` and
``sic_description`` per ticker. We may also surface a derived sector
mapping (SIC → sector group). Stubs in v1.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


def get_industry(tickers: str | Sequence[str]) -> pd.Series:
    """SIC industry description per ticker.

    Returns the ``sic_description`` field from REST ``get_ticker_details``.
    """
    raise NotImplementedError(
        "agora.equities.company.get_industry() is scaffolded but not yet "
        "implemented."
    )


def get_sector(tickers: str | Sequence[str]) -> pd.Series:
    """Sector classification per ticker (derived from SIC code groupings).

    SIC codes are 4-digit numerics; the first digit broadly corresponds to
    a sector (e.g., 1xxx = mining, 2xxx-3xxx = manufacturing). We'll
    publish the canonical SIC → sector mapping when implemented.
    """
    raise NotImplementedError(
        "agora.equities.company.get_sector() is scaffolded but not yet "
        "implemented."
    )
