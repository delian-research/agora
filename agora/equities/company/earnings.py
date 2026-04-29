"""Corporate earnings events for equities.

# TODO: Benzinga entitlement required.
#
# Source: live REST ``list_benzinga_earnings``. This is a paid Benzinga
# add-on. Stubbed in v1; implement when the add-on is enabled.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd


def get_earnings(
    tickers: str | Sequence[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Earnings event records (announcements, EPS, surprises).

    Returns:
        DataFrame with columns: ticker, period, ep_date,
        eps_estimate, eps_actual, eps_surprise_pct, etc.
    """
    raise NotImplementedError(
        "agora.equities.company.get_earnings() requires the Benzinga add-on, "
        "which is not enabled on the current Massive subscription. "
        "Stub-only in v1."
    )
