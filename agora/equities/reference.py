"""Static / point-in-time equity reference data via the Massive REST API.

Five helpers, one per Polygon endpoint:

    - :func:get_tickers         wraps /v3/reference/tickers (list)
    - :func:get_ticker_details  wraps /v3/reference/tickers/{ticker}
    - :func:get_ticker_types    wraps /v3/reference/tickers/types
    - :func:get_exchanges       wraps /v3/reference/exchanges
    - :func:get_related_tickers wraps /v1/related-companies/{ticker}

Use :func:get_tickers to discover the universe; :func:get_ticker_details
for a rich per-ticker profile (market cap, shares outstanding, SIC code,
description, etc.); :func:get_ticker_types and :func:get_exchanges as
small lookup tables for joining against the type and
primary_exchange columns; :func:get_related_tickers for similarity
graph queries.

The earlier scaffold exposed five field-specific stubs
(get_exchange, get_currency, get_country, get_market_cap,
get_shares_out). They've been removed because they would each have
called :func:get_ticker_details and discarded everything except one
field. Callers that need a single field should pull
get_ticker_details(...)[field] instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from agora.client import MassiveClient, get_client

# ── Schema ──────────────────────────────────────────────────────────

_LIST_FIELDS = (
    "ticker",
    "name",
    "market",
    "locale",
    "primary_exchange",
    "type",
    "active",
    "currency_name",
    "cik",
    "composite_figi",
    "share_class_figi",
    "last_updated_utc",
    "delisted_utc",
)

_EXCHANGE_FIELDS = (
    "id",
    "mic",
    "operating_mic",
    "name",
    "type",
    "asset_class",
    "locale",
    "acronym",
    "participant_id",
    "url",
)

_TICKER_TYPE_FIELDS = (
    "code",
    "description",
    "asset_class",
    "locale",
)

_DETAILS_FIELDS = (
    # Identity
    "ticker",
    "name",
    "cik",
    "composite_figi",
    "share_class_figi",
    "ticker_root",
    "ticker_suffix",
    # Classification
    "market",
    "locale",
    "primary_exchange",
    "type",
    "active",
    "currency_name",
    "sic_code",
    "sic_description",
    # Sizing
    "market_cap",
    "share_class_shares_outstanding",
    "weighted_shares_outstanding",
    "round_lot",
    "total_employees",
    # Profile
    "description",
    "homepage_url",
    "list_date",
    "delisted_utc",
    "phone_number",
    "address",  # nested object — kept as-is
    "branding",  # nested object — kept as-is
)


def _norm_basket(tickers: str | Sequence[str]) -> list[str]:
    if isinstance(tickers, str):
        tickers = [tickers]
    out = [t.strip().upper() for t in tickers if t and t.strip()]
    if not out:
        raise ValueError("tickers must not be empty")
    return out


def _row_from_list_record(record) -> dict:
    return {field: getattr(record, field, None) for field in _LIST_FIELDS}


def _row_from_details(record) -> dict:
    return {field: getattr(record, field, None) for field in _DETAILS_FIELDS}


# ── Public API ──────────────────────────────────────────────────────

def get_tickers(
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
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """List the universe of tickers matching the filters.

    Wraps Polygon's list-tickers endpoint with auto-pagination. Returns
    a DataFrame with one row per ticker and lightweight identifying
    fields. For the rich per-ticker profile (market cap, SIC, etc.)
    use :func:get_ticker_details.

    Args:
        market: "stocks" / "fx" / "indices" / "crypto".
            None returns all markets.
        type: Ticker type code ("CS" / "ETF" / etc.).
        active: Filter to active securities (default True). Set to
            False to retrieve delisted-only tickers.
        search: Free-text search across ticker / name.
        cik: Filter by SEC CIK number.
        date: Point-in-time universe (YYYY-MM-DD).
        sort: Sort field ("ticker" / "name" / "market").
        order: "asc" or "desc".
        limit: Per-page limit (cursor handles total result size).
        client: Override the live REST client.

    Returns:
        DataFrame with columns: ticker, name, market,
        locale, primary_exchange, type, active,
        currency_name, cik, composite_figi,
        share_class_figi, last_updated_utc, delisted_utc.

    Examples:
        >>> from agora import equities
        >>> # All active US common stocks
        >>> universe = equities.get_tickers(market="stocks", type="CS")
        >>> # All ETFs that traded on a specific date
        >>> etfs = equities.get_tickers(market="stocks", type="ETF",
        ...                              date="2024-01-03")
        >>> # Delisted-only (corporate-action research)
        >>> delisted = equities.get_tickers(active=False)
    """
    c = client or get_client()
    records = c.rest.list_tickers(
        market=market,
        type=type,
        active=active,
        search=search,
        cik=cik,
        date=date,
        sort=sort,
        order=order,
        limit=limit,
    )

    if not records:
        return pd.DataFrame(columns=list(_LIST_FIELDS))

    df = pd.DataFrame(
        [_row_from_list_record(r) for r in records],
        columns=list(_LIST_FIELDS),
    )
    return df


def get_ticker_details(
    tickers: str | Sequence[str],
    *,
    date: str | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Rich per-ticker profile: market cap, shares outstanding, SIC, etc.

    Wraps Polygon's ticker-details endpoint, looping per ticker for a
    basket. Returns one row per requested ticker with ~25 fields.

    Args:
        tickers: One or more ticker symbols.
        date: Point-in-time profile (YYYY-MM-DD). When provided,
            returns the ticker's profile as of that date.
        client: Override the live REST client.

    Returns:
        DataFrame indexed by ticker (input order) with columns:

        Identity: ticker, name, cik, composite_figi,
        share_class_figi, ticker_root, ticker_suffix.

        Classification: market, locale, primary_exchange,
        type, active, currency_name, sic_code,
        sic_description.

        Sizing: market_cap, share_class_shares_outstanding,
        weighted_shares_outstanding, round_lot, total_employees.

        Profile: description, homepage_url, list_date,
        delisted_utc, phone_number, address (nested),
        branding (nested).

        list_date and delisted_utc are returned as
        datetime64 columns. address and branding are kept
        as the raw nested objects/dicts the SDK returned — pull
        sub-fields via attribute access on those rows.

    Examples:
        >>> from agora import equities
        >>> # Single ticker
        >>> profile = equities.get_ticker_details("AAPL")
        >>> profile.iloc[0]["market_cap"]
        >>> # Basket
        >>> universe = equities.get_ticker_details(["AAPL", "MSFT", "NVDA"])
        >>> universe[["ticker", "market_cap", "sic_description"]]
        >>> # Point-in-time
        >>> hist = equities.get_ticker_details("AAPL", date="2020-01-15")
    """
    c = client or get_client()
    basket = _norm_basket(tickers)

    rows: list[dict] = []
    for t in basket:
        record = c.rest.get_ticker_details(t, date=date)
        if record is None:
            continue
        rows.append(_row_from_details(record))

    if not rows:
        return pd.DataFrame(columns=list(_DETAILS_FIELDS))

    df = pd.DataFrame(rows, columns=list(_DETAILS_FIELDS))

    # Coerce known date-like columns
    for col in ("list_date", "delisted_utc"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    return df


def get_exchanges(
    *,
    asset_class: str | None = None,
    locale: str | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """The catalog of exchanges (venues) Polygon recognizes.

    A small reference table — useful for joining against the
    primary_exchange MIC column on :func:get_tickers /
    :func:get_ticker_details.

    Args:
        asset_class: "stocks" / "options" / "crypto" / "fx".
            None returns every asset class.
        locale: "us" / "global". None returns every locale.
        client: Override the live REST client.

    Returns:
        DataFrame with columns: id, mic, operating_mic,
        name, type, asset_class, locale, acronym,
        participant_id, url.

    Examples:
        >>> from agora import equities
        >>> exchanges = equities.get_exchanges(asset_class="stocks")
        >>> exchanges[["mic", "name"]].head()
    """
    c = client or get_client()
    records = c.rest.get_exchanges(asset_class=asset_class, locale=locale)
    if not records:
        return pd.DataFrame(columns=list(_EXCHANGE_FIELDS))
    rows = [
        {field: getattr(r, field, None) for field in _EXCHANGE_FIELDS}
        for r in records
    ]
    return pd.DataFrame(rows, columns=list(_EXCHANGE_FIELDS))


def get_ticker_types(
    *,
    asset_class: str | None = None,
    locale: str | None = None,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """The catalog of ticker type codes (CS, ETF, ADRC, etc.).

    Small lookup table mapping each type code Polygon emits to a
    human-readable description and its asset class. Useful for
    documenting / joining against the type column returned by
    :func:get_tickers / :func:get_ticker_details.

    Args:
        asset_class: Filter to one asset class ("stocks" / "options" / ...).
        locale: Filter to one locale ("us" / "global").
        client: Override the live REST client.

    Returns:
        DataFrame with columns: code, description, asset_class,
        locale.

    Examples:
        >>> from agora import equities
        >>> equities.get_ticker_types(asset_class="stocks")
        # code description                       asset_class locale
        # CS   Common Stock                      stocks      us
        # ETF  Exchange Traded Fund              stocks      us
        # ADRC American Depository Receipt Common stocks     us
        # ...
    """
    c = client or get_client()
    records = c.rest.get_ticker_types(asset_class=asset_class, locale=locale)
    if not records:
        return pd.DataFrame(columns=list(_TICKER_TYPE_FIELDS))
    rows = [
        {field: getattr(r, field, None) for field in _TICKER_TYPE_FIELDS}
        for r in records
    ]
    return pd.DataFrame(rows, columns=list(_TICKER_TYPE_FIELDS))


def get_related_tickers(
    ticker: str,
    *,
    client: MassiveClient | None = None,
) -> pd.DataFrame:
    """Tickers Polygon considers similar/related to ticker.

    Per-ticker lookup against /v1/related-companies/{ticker}. The
    response is typically a small list (~10) of related ticker symbols
    that Polygon surfaces based on stock characteristics.

    Args:
        ticker: A single ticker symbol.
        client: Override the live REST client.

    Returns:
        DataFrame with one row per related ticker (column: ticker)
        plus a source_ticker column denormalized so a basket merge
        is trivial.

    Examples:
        >>> from agora import equities
        >>> equities.get_related_tickers("AAPL")
        #   ticker source_ticker
        # 0  MSFT          AAPL
        # 1  GOOGL         AAPL
        # 2  ...           AAPL
    """
    if not ticker or not isinstance(ticker, str):
        raise ValueError("ticker must be a non-empty string")
    source = ticker.strip().upper()
    if not source:
        raise ValueError("ticker must be a non-empty string")

    c = client or get_client()
    records = c.rest.get_related_companies(source)

    if not records:
        return pd.DataFrame(columns=["ticker", "source_ticker"])

    rows = [
        {"ticker": getattr(r, "ticker", None), "source_ticker": source}
        for r in records
    ]
    df = pd.DataFrame(rows, columns=["ticker", "source_ticker"])
    # Drop any rows missing a ticker symbol (defensive).
    return df[df["ticker"].notna() & (df["ticker"] != "")].reset_index(drop=True)
