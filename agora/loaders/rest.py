"""
Wrapper with retry logic.

This module provides a robust wrapper around the official Massive Python SDK
with exponential backoff retry logic, comprehensive error handling, and
response normalization utilities.
"""

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from massive import RESTClient
from massive.exceptions import AuthError, BadResponse

from agora.config import MassiveConfig
from agora.errors import MassiveAPIError, MassiveAuthenticationError, MassiveRateLimitError

# Configure logging
logger = logging.getLogger(__name__)

# Type variable for generic retry decorator
T = TypeVar('T')


def retry_with_backoff(
    max_retries: int | None = None,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to retry a function with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (None = use config default)
        initial_delay: Initial delay in seconds before first retry
        backoff_factor: Multiplier for delay after each retry
        max_delay: Maximum delay between retries in seconds

    Returns:
        Decorated function that retries on failure with exponential backoff.

    Examples:
        >>> @retry_with_backoff(max_retries=3)
        ... def fetch_data():
        ...     return api_call()
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            # Prefer the bound instance's config (avoids re-parsing .env each call);
            # fall back to a fresh MassiveConfig.from_env() for unbound usage.
            instance = args[0] if args else None
            inst_config = getattr(instance, "config", None)
            if inst_config is None:
                inst_config = MassiveConfig.from_env()
            retries = max_retries if max_retries is not None else inst_config.max_retries
            delay = initial_delay

            last_exception = None

            for attempt in range(retries + 1):
                try:
                    return func(*args, **kwargs)

                except AuthError as e:
                    # Authentication errors don't get retried
                    raise MassiveAuthenticationError(
                        "Authentication failed. Check your API key."
                    ) from e

                except BadResponse as e:
                    last_exception = e

                    # Check for rate limit (HTTP 429)
                    if hasattr(e, 'status_code') and e.status_code == 429:
                        if attempt < retries:
                            logger.warning(
                                f"Rate limit exceeded for {func.__name__}, "
                                f"retrying in {delay:.1f}s (attempt {attempt + 1}/{retries})"
                            )
                            time.sleep(delay)
                            delay = min(delay * backoff_factor, max_delay)
                            continue
                        else:
                            raise MassiveRateLimitError(
                                f"Rate limit exceeded after {retries} retries"
                            ) from e

                    # Check for authentication error (HTTP 401, 403)
                    if hasattr(e, 'status_code') and e.status_code in (401, 403):
                        raise MassiveAuthenticationError(
                            "Authentication failed. Check your API key."
                        ) from e

                    # Other errors - retry if attempts remain
                    if attempt < retries:
                        logger.warning(
                            f"Error in {func.__name__}: {e}, "
                            f"retrying in {delay:.1f}s (attempt {attempt + 1}/{retries})"
                        )
                        time.sleep(delay)
                        delay = min(delay * backoff_factor, max_delay)
                        continue
                    else:
                        raise MassiveAPIError(
                            f"API request failed after {retries} retries: {e}"
                        ) from e

                except Exception as e:
                    # Unexpected error - fail immediately
                    logger.error(f"Unexpected error in {func.__name__}: {e}")
                    raise

            # Should never reach here, but just in case
            if last_exception:
                raise MassiveAPIError(
                    f"Request failed after {retries} retries"
                ) from last_exception
            else:
                raise MassiveAPIError("Request failed for unknown reason")

        return wrapper
    return decorator


class MassiveDataApi:
    """
    Wrapper around Massive RESTClient with retry logic and error handling.

    This client provides a more robust interface to the Massive API with:
    - Automatic retry with exponential backoff
    - Comprehensive error handling
    - Configuration management
    - Response normalization

    Examples:
        >>> client = MassiveDataApi()
        >>> aggs = client.get_aggregates('AAPL', 1, 'day', '2024-01-01', '2024-12-31')
        >>> snapshot = client.get_snapshot('AAPL')
    """

    def __init__(self, config: MassiveConfig | None = None):
        """
        Initialize Massive client.

        Args:
            config: Optional MassiveConfig instance. If None, loads from environment.
        """
        if config is None:
            config = MassiveConfig.from_env()

        self.config = config
        self._client = RESTClient(api_key=config.api_key)

    @retry_with_backoff()
    def get_aggregates(
        self,
        ticker: str,
        multiplier: int,
        timespan: str,
        from_date: str,
        to_date: str,
        adjusted: bool = True,
        sort: str = "asc",
        limit: int = 50000
    ) -> list:
        """
        Get aggregate bars for a ticker with retry logic.

        Args:
            ticker: Stock ticker symbol
            multiplier: Size of timespan multiplier
            timespan: Size of time window (day, minute, hour, etc.)
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)
            adjusted: Whether to adjust for splits
            sort: Sort order (asc or desc)
            limit: Limit on number of results

        Returns:
            List of aggregate bar objects

        Raises:
            MassiveAPIError: If request fails after retries
            MassiveDataNotFoundError: If no data found for ticker
        """
        logger.debug(
            f"Fetching aggregates: {ticker} {multiplier}{timespan} "
            f"from {from_date} to {to_date}"
        )

        aggs = self._client.get_aggs(
            ticker=ticker,
            multiplier=multiplier,
            timespan=timespan,
            from_=from_date,
            to=to_date,
            adjusted=adjusted,
            sort=sort,
            limit=limit
        )
        return list(aggs)

    @retry_with_backoff()
    def get_snapshot(self, ticker: str) -> Any:
        """
        Get snapshot (current market data) for a ticker with retry logic.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Snapshot object with current market data

        Raises:
            MassiveAPIError: If request fails after retries
            MassiveDataNotFoundError: If ticker not found
        """
        logger.debug(f"Fetching snapshot for {ticker}")
        return self._client.get_snapshot_ticker("stocks", ticker)

    @retry_with_backoff()
    def get_ticker_details(self, ticker: str) -> Any:
        """
        Get detailed ticker information with retry logic.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Ticker details object

        Raises:
            MassiveAPIError: If request fails after retries
            MassiveDataNotFoundError: If ticker not found
        """
        logger.debug(f"Fetching ticker details for {ticker}")
        return self._client.get_ticker_details(ticker)

    @retry_with_backoff()
    def get_all_snapshots(self) -> list:
        """
        Get snapshots for all US-traded tickers in a single API call.

        This is dramatically faster than calling get_snapshot() per ticker
        when you need data for many tickers (e.g. full index constituents).

        Returns:
            List of snapshot objects for all available tickers.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug("Fetching all ticker snapshots")
        return list(self._client.get_snapshot_all("stocks"))

    @retry_with_backoff()
    def list_dividends(
        self,
        ticker: str | None = None,
        *,
        ex_dividend_date_gte: str | None = None,
        ex_dividend_date_lte: str | None = None,
        limit: int = 1000,
    ) -> list:
        """
        List dividend events with optional ticker / date-range filters.

        The SDK auto-paginates; the returned list contains every record
        the cursor walks through.

        Args:
            ticker: Single ticker symbol. ``None`` returns all dividends.
            ex_dividend_date_gte: Earliest ex-dividend date (YYYY-MM-DD).
            ex_dividend_date_lte: Latest ex-dividend date (YYYY-MM-DD).
            limit: Per-page limit (cursor handles total result size).

        Returns:
            List of dividend objects with attributes ``ticker``,
            ``ex_dividend_date``, ``pay_date``, ``record_date``,
            ``declaration_date``, ``cash_amount``, ``currency``,
            ``frequency``, ``dividend_type``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching dividends: ticker=%s ex_div=[%s, %s]",
            ticker, ex_dividend_date_gte, ex_dividend_date_lte,
        )
        kwargs: dict = {"limit": limit}
        if ticker is not None:
            kwargs["ticker"] = ticker
        if ex_dividend_date_gte is not None:
            kwargs["ex_dividend_date_gte"] = ex_dividend_date_gte
        if ex_dividend_date_lte is not None:
            kwargs["ex_dividend_date_lte"] = ex_dividend_date_lte
        return list(self._client.list_dividends(**kwargs))

    @retry_with_backoff()
    def list_splits(
        self,
        ticker: str | None = None,
        *,
        execution_date_gte: str | None = None,
        execution_date_lte: str | None = None,
        limit: int = 1000,
    ) -> list:
        """
        List stock split events with optional ticker / date-range filters.

        Args:
            ticker: Single ticker symbol. ``None`` returns all splits.
            execution_date_gte: Earliest execution date (YYYY-MM-DD).
            execution_date_lte: Latest execution date (YYYY-MM-DD).
            limit: Per-page limit (cursor handles total result size).

        Returns:
            List of split objects with attributes ``ticker``,
            ``execution_date``, ``split_from``, ``split_to``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching splits: ticker=%s exec=[%s, %s]",
            ticker, execution_date_gte, execution_date_lte,
        )
        kwargs: dict = {"limit": limit}
        if ticker is not None:
            kwargs["ticker"] = ticker
        if execution_date_gte is not None:
            kwargs["execution_date_gte"] = execution_date_gte
        if execution_date_lte is not None:
            kwargs["execution_date_lte"] = execution_date_lte
        return list(self._client.list_splits(**kwargs))

    @retry_with_backoff()
    def get_grouped_daily_aggs(
        self,
        date: str,
        *,
        adjusted: bool = True,
        include_otc: bool = False,
    ) -> list:
        """
        Get all-tickers cross-section of daily OHLCV for one date.

        One API call returns ~10K bar objects (every active stock that
        traded on ``date``). Dramatically faster than per-ticker
        :meth:`get_aggregates` when you need a wide universe over a
        small date window.

        Args:
            date: Trading date (YYYY-MM-DD). Returns 0 results for
                weekends / holidays.
            adjusted: Whether prices are split-adjusted.
            include_otc: Whether to include OTC securities.

        Returns:
            List of bar objects with attributes ``ticker``, ``open``,
            ``high``, ``low``, ``close``, ``volume``, ``vwap``,
            ``transactions``, ``timestamp``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching grouped daily aggs: date=%s adjusted=%s otc=%s",
            date, adjusted, include_otc,
        )
        return list(self._client.get_grouped_daily_aggs(
            date,
            adjusted=adjusted,
            include_otc=include_otc,
        ))

    def close(self):
        """Close the client connection."""
        # The REST client doesn't need explicit closing, but this is here
        # for API compatibility and future-proofing
        pass

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False

