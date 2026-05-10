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
    def get_ticker_details(self, ticker: str, *, date: str | None = None) -> Any:
        """
        Get detailed ticker information with retry logic.

        Args:
            ticker: Stock ticker symbol.
            date: Point-in-time profile (YYYY-MM-DD). When provided,
                returns the ticker's profile *as of* that date — name,
                market_cap, sic_code, etc. as recorded by Polygon then.
                ``None`` returns the current profile.

        Returns:
            Ticker details object with attributes including ``ticker``,
            ``name``, ``market``, ``locale``, ``primary_exchange``,
            ``type``, ``active``, ``currency_name``, ``cik``,
            ``composite_figi``, ``share_class_figi``, ``market_cap``,
            ``share_class_shares_outstanding``,
            ``weighted_shares_outstanding``, ``sic_code``,
            ``sic_description``, ``description``, ``homepage_url``,
            ``list_date``, ``phone_number``, ``address``, ``branding``,
            ``total_employees``, ``round_lot``.

        Raises:
            MassiveAPIError: If request fails after retries.
            MassiveDataNotFoundError: If ticker not found.
        """
        logger.debug(f"Fetching ticker details for {ticker} (date={date})")
        if date is not None:
            return self._client.get_ticker_details(ticker, date=date)
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
    def get_exchanges(
        self,
        *,
        asset_class: str | None = None,
        locale: str | None = None,
    ) -> list:
        """
        List the catalog of exchanges (venues) Polygon recognizes.

        Wraps ``/v3/reference/exchanges``. Single API call returns ~50
        exchange records.

        Args:
            asset_class: ``"stocks"`` / ``"options"`` / ``"crypto"`` / ``"fx"``.
                ``None`` returns every asset class.
            locale: ``"us"`` / ``"global"``. ``None`` returns every locale.

        Returns:
            List of exchange objects with attributes ``id``, ``mic``,
            ``operating_mic``, ``name``, ``type``, ``asset_class``,
            ``locale``, ``acronym``, ``participant_id``, ``url``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching exchanges: asset_class=%s locale=%s", asset_class, locale,
        )
        kwargs: dict = {}
        if asset_class is not None:
            kwargs["asset_class"] = asset_class
        if locale is not None:
            kwargs["locale"] = locale
        return list(self._client.get_exchanges(**kwargs))

    @retry_with_backoff()
    def get_ticker_types(
        self,
        *,
        asset_class: str | None = None,
        locale: str | None = None,
    ) -> list:
        """
        List the catalog of ticker type codes (CS, ETF, ADRC, etc.).

        Wraps ``/v3/reference/tickers/types``. Single API call returns the
        small lookup table that maps each ``type`` code to a human-readable
        description and asset class. Useful for joining against the
        ``type`` field on :meth:`list_tickers` / :meth:`get_ticker_details`.

        Args:
            asset_class: Filter to one asset class.
            locale: Filter to one locale.

        Returns:
            List of ticker-type objects with attributes ``code``,
            ``description``, ``asset_class``, ``locale``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching ticker types: asset_class=%s locale=%s",
            asset_class, locale,
        )
        kwargs: dict = {}
        if asset_class is not None:
            kwargs["asset_class"] = asset_class
        if locale is not None:
            kwargs["locale"] = locale
        return list(self._client.get_ticker_types(**kwargs))

    # ── Market state ─────────────────────────────────────────────────

    @retry_with_backoff()
    def get_market_status(self) -> Any:
        """Current open/closed status across exchanges + currencies + crypto.

        Wraps ``/v1/marketstatus/now``. Returns a single ``MarketStatus``
        object with attributes ``after_hours``, ``early_hours``,
        ``market``, ``server_time``, plus nested ``exchanges`` and
        ``currencies`` objects.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug("Fetching market status")
        return self._client.get_market_status()

    @retry_with_backoff()
    def get_market_holidays(self) -> list:
        """List upcoming market holidays.

        Wraps ``/v1/marketstatus/upcoming``. Returns a list of
        ``MarketHoliday`` objects with attributes ``date``, ``name``,
        ``exchange``, ``status`` (e.g. ``"closed"`` / ``"early-close"``),
        ``open``, ``close``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug("Fetching market holidays")
        return list(self._client.get_market_holidays())

    # ── Live ticks ───────────────────────────────────────────────────

    @retry_with_backoff()
    def get_last_trade(self, ticker: str) -> Any:
        """Most recent trade for ``ticker``.

        Wraps ``/v2/last/trade/{ticker}``. Returns a ``LastTrade`` object
        with attributes ``ticker``, ``price``, ``size``, ``exchange``,
        ``conditions``, ``sip_timestamp``, ``participant_timestamp``,
        ``trf_timestamp``, ``id``, ``sequence_number``, ``tape``,
        ``correction``, ``fractional_size``, ``trf_id``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(f"Fetching last trade for {ticker}")
        return self._client.get_last_trade(ticker)

    @retry_with_backoff()
    def get_last_quote(self, ticker: str) -> Any:
        """Most recent NBBO quote for ``ticker``.

        Wraps ``/v2/last/nbbo/{ticker}``. Returns a ``LastQuote`` object
        with attributes ``ticker``, ``bid_price``, ``bid_size``,
        ``bid_exchange``, ``ask_price``, ``ask_size``, ``ask_exchange``,
        ``conditions``, ``indicators``, ``sip_timestamp``,
        ``participant_timestamp``, ``trf_timestamp``, ``sequence_number``,
        ``tape``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(f"Fetching last quote for {ticker}")
        return self._client.get_last_quote(ticker)

    @retry_with_backoff()
    def get_previous_close_agg(
        self, ticker: str, *, adjusted: bool = True,
    ) -> Any:
        """Previous trading day's bar for ``ticker``.

        Wraps ``/v2/aggs/ticker/{ticker}/prev``. Returns a
        ``PreviousCloseAgg`` object with attributes ``ticker``, ``open``,
        ``high``, ``low``, ``close``, ``volume``, ``vwap``, ``timestamp``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(f"Fetching previous close for {ticker}")
        return self._client.get_previous_close_agg(ticker, adjusted=adjusted)

    # ── Fundamentals ─────────────────────────────────────────────────

    @retry_with_backoff()
    def list_financials_balance_sheets(
        self,
        *,
        tickers: str | None = None,
        cik: str | None = None,
        period_end_gte: str | None = None,
        period_end_lte: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List balance-sheet records for tickers/period.

        Wraps ``/v3/reference/financials/balance-sheets``. Auto-paginated.
        Returns ``FinancialBalanceSheet`` objects with ~30 line items.

        Args:
            tickers: One ticker or comma-separated list (string).
            cik: SEC CIK (alternative to ticker).
            period_end_gte / period_end_lte: Period-end filter (YYYY-MM-DD).
            timeframe: ``"annual"`` / ``"quarterly"``.
            limit: Per-page limit; SDK paginates beyond.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching balance sheets: tickers=%s cik=%s timeframe=%s",
            tickers, cik, timeframe,
        )
        kwargs: dict = {}
        if tickers is not None:
            kwargs["tickers"] = tickers
        if cik is not None:
            kwargs["cik"] = cik
        if period_end_gte is not None:
            kwargs["period_end_gte"] = period_end_gte
        if period_end_lte is not None:
            kwargs["period_end_lte"] = period_end_lte
        if timeframe is not None:
            kwargs["timeframe"] = timeframe
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.list_financials_balance_sheets(**kwargs))

    @retry_with_backoff()
    def list_financials_cash_flow_statements(
        self,
        *,
        tickers: str | None = None,
        cik: str | None = None,
        period_end_gte: str | None = None,
        period_end_lte: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List cash-flow statements. Wraps ``/v3/reference/financials/cash-flow-statements``."""
        logger.debug(
            "Fetching cash flow statements: tickers=%s cik=%s timeframe=%s",
            tickers, cik, timeframe,
        )
        kwargs: dict = {}
        if tickers is not None:
            kwargs["tickers"] = tickers
        if cik is not None:
            kwargs["cik"] = cik
        if period_end_gte is not None:
            kwargs["period_end_gte"] = period_end_gte
        if period_end_lte is not None:
            kwargs["period_end_lte"] = period_end_lte
        if timeframe is not None:
            kwargs["timeframe"] = timeframe
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.list_financials_cash_flow_statements(**kwargs))

    @retry_with_backoff()
    def list_financials_income_statements(
        self,
        *,
        tickers: str | None = None,
        cik: str | None = None,
        period_end_gte: str | None = None,
        period_end_lte: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List income statements. Wraps ``/v3/reference/financials/income-statements``."""
        logger.debug(
            "Fetching income statements: tickers=%s cik=%s timeframe=%s",
            tickers, cik, timeframe,
        )
        kwargs: dict = {}
        if tickers is not None:
            kwargs["tickers"] = tickers
        if cik is not None:
            kwargs["cik"] = cik
        if period_end_gte is not None:
            kwargs["period_end_gte"] = period_end_gte
        if period_end_lte is not None:
            kwargs["period_end_lte"] = period_end_lte
        if timeframe is not None:
            kwargs["timeframe"] = timeframe
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.list_financials_income_statements(**kwargs))

    @retry_with_backoff()
    def list_financials_ratios(
        self,
        *,
        ticker: str | None = None,
        cik: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List point-in-time financial ratios for a ticker.

        Wraps ``/v3/reference/financials/ratios``. Returns rows with
        market-derived ratios (P/E, P/B, dividend_yield, debt_to_equity,
        ev_to_ebitda, etc.) per snapshot date. Daily updates.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug("Fetching ratios: ticker=%s cik=%s", ticker, cik)
        kwargs: dict = {}
        if ticker is not None:
            kwargs["ticker"] = ticker
        if cik is not None:
            kwargs["cik"] = cik
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.list_financials_ratios(**kwargs))

    # ── Short data ───────────────────────────────────────────────────

    @retry_with_backoff()
    def list_short_interest(
        self,
        *,
        ticker: str | None = None,
        settlement_date_gte: str | None = None,
        settlement_date_lte: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List short-interest records (bi-monthly settlement dates).

        Wraps ``/stocks/v1/short-interest``. Each record carries
        ``ticker``, ``settlement_date``, ``short_interest``,
        ``avg_daily_volume``, ``days_to_cover``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching short interest: ticker=%s settle=[%s, %s]",
            ticker, settlement_date_gte, settlement_date_lte,
        )
        kwargs: dict = {}
        if ticker is not None:
            kwargs["ticker"] = ticker
        if settlement_date_gte is not None:
            kwargs["settlement_date_gte"] = settlement_date_gte
        if settlement_date_lte is not None:
            kwargs["settlement_date_lte"] = settlement_date_lte
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.list_short_interest(**kwargs))

    @retry_with_backoff()
    def list_short_volume(
        self,
        *,
        ticker: str | None = None,
        date_gte: str | None = None,
        date_lte: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List daily short-volume records.

        Wraps ``/stocks/v1/short-volume``. Each record carries
        ``ticker``, ``date``, ``short_volume``, ``total_volume``,
        ``short_volume_ratio`` plus per-venue breakouts.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching short volume: ticker=%s date=[%s, %s]",
            ticker, date_gte, date_lte,
        )
        kwargs: dict = {}
        if ticker is not None:
            kwargs["ticker"] = ticker
        if date_gte is not None:
            kwargs["date_gte"] = date_gte
        if date_lte is not None:
            kwargs["date_lte"] = date_lte
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.list_short_volume(**kwargs))

    @retry_with_backoff()
    def list_stocks_floats(
        self,
        *,
        ticker: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List stock-float records.

        Wraps ``/stocks/v1/floats``. Each record carries ``ticker``,
        ``effective_date``, ``free_float``, ``free_float_percent``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(f"Fetching floats: ticker={ticker}")
        kwargs: dict = {}
        if ticker is not None:
            kwargs["ticker"] = ticker
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.list_stocks_floats(**kwargs))

    # ── ETF (ETF Global feed) ────────────────────────────────────────

    @retry_with_backoff()
    def get_etf_global_constituents(
        self,
        *,
        composite_ticker: str | None = None,
        constituent_ticker: str | None = None,
        effective_date: str | None = None,
        effective_date_gte: str | None = None,
        effective_date_lte: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List ETF holdings (constituents).

        Wraps ETF Global's constituents endpoint. Each record carries
        ``composite_ticker`` (the ETF), ``constituent_ticker`` (the
        underlying), ``shares_held``, ``weight``, ``market_value``,
        plus identifiers (ISIN/SEDOL/FIGI).

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching ETF constituents: etf=%s constituent=%s effective_date=%s",
            composite_ticker, constituent_ticker, effective_date,
        )
        kwargs: dict = {}
        if composite_ticker is not None:
            kwargs["composite_ticker"] = composite_ticker
        if constituent_ticker is not None:
            kwargs["constituent_ticker"] = constituent_ticker
        if effective_date is not None:
            kwargs["effective_date"] = effective_date
        if effective_date_gte is not None:
            kwargs["effective_date_gte"] = effective_date_gte
        if effective_date_lte is not None:
            kwargs["effective_date_lte"] = effective_date_lte
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.get_etf_global_constituents(**kwargs))

    @retry_with_backoff()
    def get_etf_global_fund_flows(
        self,
        *,
        composite_ticker: str | None = None,
        effective_date: str | None = None,
        effective_date_gte: str | None = None,
        effective_date_lte: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List ETF daily net fund flows.

        Each record carries ``composite_ticker``, ``effective_date``,
        ``fund_flow`` (USD), ``nav``, ``shares_outstanding``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching ETF fund flows: etf=%s effective_date=%s",
            composite_ticker, effective_date,
        )
        kwargs: dict = {}
        if composite_ticker is not None:
            kwargs["composite_ticker"] = composite_ticker
        if effective_date is not None:
            kwargs["effective_date"] = effective_date
        if effective_date_gte is not None:
            kwargs["effective_date_gte"] = effective_date_gte
        if effective_date_lte is not None:
            kwargs["effective_date_lte"] = effective_date_lte
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.get_etf_global_fund_flows(**kwargs))

    @retry_with_backoff()
    def get_etf_global_profiles(
        self,
        *,
        composite_ticker: str | None = None,
        effective_date: str | None = None,
        effective_date_gte: str | None = None,
        effective_date_lte: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List ETF profile metadata (issuer, AUM, fees, exposure, etc.).

        ETF Global's profile endpoint surfaces ~50 metadata fields per
        ETF including ``issuer``, ``aum``, ``creation_unit_size``,
        ``distribution_frequency``, ``industry_exposure``, etc.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching ETF profiles: etf=%s effective_date=%s",
            composite_ticker, effective_date,
        )
        kwargs: dict = {}
        if composite_ticker is not None:
            kwargs["composite_ticker"] = composite_ticker
        if effective_date is not None:
            kwargs["effective_date"] = effective_date
        if effective_date_gte is not None:
            kwargs["effective_date_gte"] = effective_date_gte
        if effective_date_lte is not None:
            kwargs["effective_date_lte"] = effective_date_lte
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.get_etf_global_profiles(**kwargs))

    @retry_with_backoff()
    def get_etf_global_analytics(
        self,
        *,
        composite_ticker: str | None = None,
        effective_date: str | None = None,
        effective_date_gte: str | None = None,
        effective_date_lte: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List ETF quant scores / analytics (risk, reward, fundamental, etc.).

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching ETF analytics: etf=%s effective_date=%s",
            composite_ticker, effective_date,
        )
        kwargs: dict = {}
        if composite_ticker is not None:
            kwargs["composite_ticker"] = composite_ticker
        if effective_date is not None:
            kwargs["effective_date"] = effective_date
        if effective_date_gte is not None:
            kwargs["effective_date_gte"] = effective_date_gte
        if effective_date_lte is not None:
            kwargs["effective_date_lte"] = effective_date_lte
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.get_etf_global_analytics(**kwargs))

    @retry_with_backoff()
    def get_etf_global_taxonomies(
        self,
        *,
        composite_ticker: str | None = None,
        effective_date: str | None = None,
        effective_date_gte: str | None = None,
        effective_date_lte: str | None = None,
        limit: int | None = None,
    ) -> list:
        """List ETF taxonomies (asset_class, category, focus, etc.).

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching ETF taxonomies: etf=%s effective_date=%s",
            composite_ticker, effective_date,
        )
        kwargs: dict = {}
        if composite_ticker is not None:
            kwargs["composite_ticker"] = composite_ticker
        if effective_date is not None:
            kwargs["effective_date"] = effective_date
        if effective_date_gte is not None:
            kwargs["effective_date_gte"] = effective_date_gte
        if effective_date_lte is not None:
            kwargs["effective_date_lte"] = effective_date_lte
        if limit is not None:
            kwargs["limit"] = limit
        return list(self._client.get_etf_global_taxonomies(**kwargs))

    @retry_with_backoff()
    def get_related_companies(self, ticker: str) -> list:
        """
        Get tickers Polygon considers similar/related to ``ticker``.

        Wraps ``/v1/related-companies/{ticker}``. Single per-ticker call;
        the response is a small list of related ticker symbols (typically
        ~10).

        Args:
            ticker: Stock ticker symbol.

        Returns:
            List of related-ticker objects with at least a ``ticker``
            attribute.

        Raises:
            MassiveAPIError: If request fails after retries.
            MassiveDataNotFoundError: If ticker not found.
        """
        logger.debug(f"Fetching related companies for {ticker}")
        return list(self._client.get_related_companies(ticker))

    @retry_with_backoff()
    def list_tickers(
        self,
        *,
        market: str | None = None,
        type: str | None = None,
        active: bool = True,
        search: str | None = None,
        cik: str | None = None,
        date: str | None = None,
        sort: str = "ticker",
        order: str = "asc",
        limit: int = 1000,
    ) -> list:
        """
        List ticker reference records with optional filters.

        Wraps Polygon's ``/v3/reference/tickers``. The SDK auto-paginates
        via cursor; the returned list contains every record across pages.

        Args:
            market: ``"stocks"`` / ``"fx"`` / ``"indices"`` / ``"crypto"``.
            type: Ticker type code (``"CS"`` / ``"ETF"`` / ``"ADRC"`` / etc.).
            active: Filter to active securities (default ``True``).
            search: Free-text search across ticker / name.
            cik: Filter by SEC CIK number.
            date: Point-in-time universe (YYYY-MM-DD). Returns the
                set of tickers active on that date.
            sort: Sort field (``"ticker"`` / ``"name"`` / ``"market"``).
            order: ``"asc"`` or ``"desc"``.
            limit: Per-page limit (cursor handles total result size).

        Returns:
            List of ticker reference objects with attributes ``ticker``,
            ``name``, ``market``, ``locale``, ``primary_exchange``,
            ``type``, ``active``, ``currency_name``, ``cik``,
            ``composite_figi``, ``share_class_figi``,
            ``last_updated_utc``, ``delisted_utc``.

        Raises:
            MassiveAPIError: If request fails after retries.
        """
        logger.debug(
            "Fetching tickers: market=%s type=%s active=%s",
            market, type, active,
        )
        kwargs: dict = {
            "active": active,
            "sort": sort,
            "order": order,
            "limit": limit,
        }
        if market is not None:
            kwargs["market"] = market
        if type is not None:
            kwargs["type"] = type
        if search is not None:
            kwargs["search"] = search
        if cik is not None:
            kwargs["cik"] = cik
        if date is not None:
            kwargs["date"] = date
        return list(self._client.list_tickers(**kwargs))

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

