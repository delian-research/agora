"""Company-level equity attributes — classification, news, earnings.

Public functions:
    - :func:`get_industry`, :func:`get_sector`  (classification — stubs)
    - :func:`get_major_news`                     (Benzinga — stub; entitlement required)
    - :func:`get_earnings`                       (Benzinga — stub; entitlement required)

# TODO: Benzinga entitlement required for `get_major_news` and
# `get_earnings`. Implement when the Benzinga add-on is enabled on
# the Massive account.
"""

from agora.equities.company.classification import get_industry, get_sector
from agora.equities.company.earnings import get_earnings
from agora.equities.company.news import get_major_news

__all__ = [
    "get_industry",
    "get_sector",
    "get_major_news",
    "get_earnings",
]
