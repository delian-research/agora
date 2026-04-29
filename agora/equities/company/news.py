"""Major news events for equities.

# TODO: Benzinga entitlement required.
#
# Source: live REST ``list_benzinga_news_v2``. This is a paid Benzinga
# add-on, separate from the Stocks Starter / Currencies / Indices
# subscriptions. Stubbed in v1; implement when the add-on is enabled.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd


def get_major_news(
    tickers: str | Sequence[str] | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
) -> pd.DataFrame:
    """Recent news articles tagged to one or more tickers.

    Returns:
        DataFrame with columns covering article id, headline, body,
        published timestamp, source, and ticker tags.
    """
    raise NotImplementedError(
        "agora.equities.company.get_major_news() requires the Benzinga add-on, "
        "which is not enabled on the current Massive subscription. "
        "Stub-only in v1."
    )
