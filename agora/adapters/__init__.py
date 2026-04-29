"""Analytics-shaped helpers built on top of the loaders.

These convert raw loader output into the shapes most quant workflows
expect — pivoted price matrices, return series, etc.
"""

from agora.adapters.market import get_prices, get_returns

__all__ = [
    "get_prices",
    "get_returns",
]
