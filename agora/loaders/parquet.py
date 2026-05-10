"""
Loader for the local Parquet data store.

This module reads from the local data/ directory containing Parquet files
downloaded by `agora.download` (whose stocks pipeline pulls from Massive's
S3 flat-file endpoint, hence the historical name `s3.py` for this loader).
It provides fast, offline access to historical stock prices, forex rates,
and reference data without any API calls or rate limits.

The data directory layout it expects:

    data/
    ├── stocks/daily/{year}.parquet
    ├── forex/daily_usd.parquet
    └── reference/
        ├── tickers.parquet
        ├── exchanges.parquet
        ├── splits.parquet
        ├── dividends.parquet
        └── ticker_events.parquet

Examples:
    >>> loader = FlatFileLoader()
    >>> prices = loader.get_prices(["AAPL", "MSFT"], start="2024-01-01", end="2024-12-31")
    >>> rates = loader.get_forex(["C:EURUSD", "C:GBPUSD"])
    >>> history = loader.get_ticker_history("META")  # includes FB → META rename
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)

# Default data directory (relative to project root)
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class FlatFileLoader:
    """Read-only access to the local Parquet data store.

    Args:
        data_dir: Path to the data directory. Defaults to ``<project>/data/``.

    Examples:
        >>> loader = FlatFileLoader()
        >>> df = loader.get_prices("AAPL", start="2023-01-01", end="2023-12-31")
        >>> df.head()
    """

    def __init__(self, data_dir: Path | str | None = None):
        self._data_dir = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
        self._stock_cache: dict[int, pd.DataFrame] = {}

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    # ── Stock Prices ─────────────────────────────────────────────────

    def _load_stock_year(self, year: int) -> pd.DataFrame:
        """Load and cache a single year of stock data."""
        if year not in self._stock_cache:
            path = self._data_dir / "stocks" / "daily" / f"{year}.parquet"
            if not path.exists():
                return pd.DataFrame()
            self._stock_cache[year] = pd.read_parquet(path)
        return self._stock_cache[year]

    def available_stock_years(self) -> list[int]:
        """Return list of years with downloaded stock data."""
        stock_dir = self._data_dir / "stocks" / "daily"
        if not stock_dir.exists():
            return []
        return sorted(
            int(f.stem) for f in stock_dir.glob("*.parquet") if f.stem.isdigit()
        )

    def get_stock_daily(
        self,
        tickers: str | Sequence[str] | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Load stock daily OHLCV data from local Parquet files.

        Args:
            tickers: One or more ticker symbols to filter. None = all tickers.
            start: Start date (YYYY-MM-DD inclusive). None = earliest available.
            end: End date (YYYY-MM-DD inclusive). None = latest available.

        Returns:
            DataFrame with columns: date, ticker, open, high, low, close, volume, trades.

        Note:
            Flat file prices are NOT split-adjusted. Apply splits from
            ``get_splits()`` if you need adjusted prices.
        """
        if isinstance(tickers, str):
            tickers = [tickers]

        # Determine which year files to load
        years = self.available_stock_years()
        if start:
            years = [y for y in years if y >= int(start[:4])]
        if end:
            years = [y for y in years if y <= int(end[:4])]

        if not years:
            return pd.DataFrame()

        frames = [self._load_stock_year(y) for y in years]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame()

        df = pd.concat(frames, ignore_index=True)

        if tickers:
            tickers_upper = [t.upper() for t in tickers]
            df = df[df["ticker"].isin(tickers_upper)]

        if start:
            df = df[df["date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["date"] <= pd.Timestamp(end)]

        return df.sort_values(["date", "ticker"]).reset_index(drop=True)

    def get_prices(
        self,
        tickers: str | Sequence[str],
        start: str | None = None,
        end: str | None = None,
        field: Literal["open", "high", "low", "close"] = "close",
    ) -> pd.DataFrame:
        """Get a pivoted price matrix (date × ticker) for quick analysis.

        Args:
            tickers: One or more ticker symbols.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).
            field: Price field to pivot (default: close).

        Returns:
            DataFrame indexed by date with one column per ticker.

        Examples:
            >>> loader = FlatFileLoader()
            >>> prices = loader.get_prices(["AAPL", "MSFT"], start="2024-01-01")
            >>> prices.pct_change().corr()
        """
        if isinstance(tickers, str):
            tickers = [tickers]

        df = self.get_stock_daily(tickers, start, end)
        if df.empty:
            return pd.DataFrame()

        pivot = df.pivot_table(index="date", columns="ticker", values=field)
        # Reorder columns to match input order
        cols = [t.upper() for t in tickers if t.upper() in pivot.columns]
        return pivot[cols]

    # ── Forex ────────────────────────────────────────────────────────

    def get_forex(
        self,
        pairs: str | Sequence[str] | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Load forex daily data from local Parquet.

        Args:
            pairs: One or more pair symbols (e.g., "C:EURUSD"). None = all pairs.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).

        Returns:
            DataFrame with columns: date, ticker, open, high, low, close, volume, trades.
        """
        path = self._data_dir / "forex" / "daily_usd.parquet"
        if not path.exists():
            return pd.DataFrame()

        df = pd.read_parquet(path)

        if pairs:
            if isinstance(pairs, str):
                pairs = [pairs]
            pairs_upper = [p.upper() for p in pairs]
            df = df[df["ticker"].isin(pairs_upper)]

        if start:
            df = df[df["date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["date"] <= pd.Timestamp(end)]

        return df.sort_values(["date", "ticker"]).reset_index(drop=True)

    def get_fx_rates(
        self,
        currencies: str | Sequence[str] | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Get a pivoted FX rate matrix (date × currency) using close prices.

        Args:
            currencies: ISO currency codes (e.g., "EUR", "GBP").
                Automatically mapped to C:XXXUSD pair tickers.
                None = all available currencies.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).

        Returns:
            DataFrame indexed by date with one column per currency code,
            values are units of foreign currency per 1 USD.

        Examples:
            >>> loader = FlatFileLoader()
            >>> rates = loader.get_fx_rates(["EUR", "GBP", "JPY"])
        """
        if currencies:
            if isinstance(currencies, str):
                currencies = [currencies]
            pairs = [f"C:{c.upper()}USD" for c in currencies]
        else:
            pairs = None

        df = self.get_forex(pairs, start, end)
        if df.empty:
            return pd.DataFrame()

        pivot = df.pivot_table(index="date", columns="ticker", values="close")
        # Rename columns from C:XXXUSD → XXX
        pivot.columns = [c.replace("C:", "").replace("USD", "") for c in pivot.columns]
        return pivot

    # ── Reference Data ───────────────────────────────────────────────

    def get_tickers(
        self,
        market: str | None = None,
        ticker_type: str | None = None,
    ) -> pd.DataFrame:
        """Load ticker reference data.

        Args:
            market: Filter by market ("stocks" or "fx"). None = all.
            ticker_type: Filter by type code ("CS", "ETF", etc.). None = all.
        """
        path = self._data_dir / "reference" / "tickers.parquet"
        if not path.exists():
            return pd.DataFrame()

        df = pd.read_parquet(path)
        if market:
            df = df[df["market"] == market]
        if ticker_type:
            df = df[df["type"] == ticker_type]
        return df

    def get_exchanges(self) -> pd.DataFrame:
        """Load exchange reference data."""
        path = self._data_dir / "reference" / "exchanges.parquet"
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    def get_splits(
        self,
        tickers: str | Sequence[str] | None = None,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Load stock splits with optional ticker basket and date-range filters.

        Args:
            tickers: One or more ticker symbols, or ``None`` for all.
            start: Earliest execution date (YYYY-MM-DD inclusive).
            end:   Latest execution date (YYYY-MM-DD inclusive).
        """
        return self._load_event_frame(
            "reference/splits.parquet",
            date_col="execution_date",
            tickers=tickers,
            start=start,
            end=end,
        )

    def get_dividends(
        self,
        tickers: str | Sequence[str] | None = None,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Load stock dividends with optional ticker basket and date-range filters.

        Args:
            tickers: One or more ticker symbols, or ``None`` for all.
            start: Earliest ex-dividend date (YYYY-MM-DD inclusive).
            end:   Latest ex-dividend date (YYYY-MM-DD inclusive).
        """
        return self._load_event_frame(
            "reference/dividends.parquet",
            date_col="ex_dividend_date",
            tickers=tickers,
            start=start,
            end=end,
        )

    def _load_event_frame(
        self,
        relpath: str,
        *,
        date_col: str,
        tickers: str | Sequence[str] | None,
        start: str | None,
        end: str | None,
    ) -> pd.DataFrame:
        """Shared loader for ticker-keyed event tables (dividends, splits)."""
        path = self._data_dir / relpath
        if not path.exists():
            return pd.DataFrame()

        df = pd.read_parquet(path)
        if df.empty:
            return df

        if tickers is not None:
            if isinstance(tickers, str):
                tickers = [tickers]
            wanted = {t.upper() for t in tickers if t and t.strip()}
            df = df[df["ticker"].isin(wanted)]

        if start:
            df = df[df[date_col] >= pd.Timestamp(start)]
        if end:
            df = df[df[date_col] <= pd.Timestamp(end)]

        return df.sort_values([date_col, "ticker"]).reset_index(drop=True)

    # ── Security Master ──────────────────────────────────────────────

    def get_ticker_events(self, ticker: str | None = None) -> pd.DataFrame:
        """Load the security master (ticker events with valid_from/valid_to).

        Args:
            ticker: Filter to events for a specific ticker symbol.
                Searches both current and historical tickers.

        Returns:
            DataFrame with columns: ticker, composite_figi, cik, name,
            valid_from, valid_to, is_current.
        """
        path = self._data_dir / "reference" / "ticker_events.parquet"
        if not path.exists():
            return pd.DataFrame()

        df = pd.read_parquet(path)
        if ticker:
            t = ticker.upper()
            df = df[df["ticker"] == t]
        return df

    def get_ticker_history(self, ticker: str) -> pd.DataFrame:
        """Get the full identity chain for a security.

        Given any ticker (current or historical), returns all ticker symbols
        that have been used for that security over time, linked by FIGI.

        Args:
            ticker: Any ticker symbol (current or historical).

        Returns:
            DataFrame of all ticker identities for that security, sorted by valid_from.

        Examples:
            >>> loader = FlatFileLoader()
            >>> loader.get_ticker_history("FB")
            # Returns: FB (2012-05-18 → 2022-06-09), META (2022-06-09 → current)
        """
        events = self.get_ticker_events()
        if events.empty:
            return pd.DataFrame()

        t = ticker.upper()
        # Find the FIGI for this ticker
        match = events[events["ticker"] == t]
        if match.empty:
            return pd.DataFrame()

        figi = match["composite_figi"].iloc[0]
        # Return all tickers for this FIGI
        chain = events[events["composite_figi"] == figi]
        return chain.sort_values("valid_from").reset_index(drop=True)

    def resolve_ticker(self, ticker: str, date: str) -> str | None:
        """Resolve a current ticker to what it was called on a given date.

        Args:
            ticker: Current (or any known) ticker symbol.
            date: Date to resolve (YYYY-MM-DD).

        Returns:
            The ticker symbol that was active on that date, or None if unknown.

        Examples:
            >>> loader = FlatFileLoader()
            >>> loader.resolve_ticker("META", "2020-06-15")
            'FB'
        """
        chain = self.get_ticker_history(ticker)
        if chain.empty:
            return None

        dt = pd.Timestamp(date)
        for _, row in chain.iterrows():
            after_start = pd.isna(row["valid_from"]) or dt >= row["valid_from"]
            before_end = pd.isna(row["valid_to"]) or dt < row["valid_to"]
            if after_start and before_end:
                return row["ticker"]

        return None

    def get_continuous_prices(
        self,
        ticker: str,
        start: str | None = None,
        end: str | None = None,
        field: Literal["open", "high", "low", "close"] = "close",
    ) -> pd.Series:
        """Get a continuous price series for a security across ticker changes.

        Uses the security master to stitch together price history across
        symbol changes (e.g., FB → META).

        Args:
            ticker: Any known ticker symbol (current or historical).
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).
            field: Price field (default: close).

        Returns:
            Series indexed by date with continuous prices, named by current ticker.

        Examples:
            >>> loader = FlatFileLoader()
            >>> meta_prices = loader.get_continuous_prices("META", start="2021-06-01")
            # Includes data under both FB and META symbols
        """
        chain = self.get_ticker_history(ticker)

        if chain.empty:
            # No events found — try direct lookup
            df = self.get_stock_daily(ticker, start, end)
            if df.empty:
                return pd.Series(dtype="float64")
            return df.set_index("date")[field].rename(ticker.upper())

        # Collect prices for each ticker identity in the chain
        frames = []
        for _, row in chain.iterrows():
            t = row["ticker"]
            seg_start = start
            seg_end = end

            if pd.notna(row["valid_from"]):
                vf = row["valid_from"].strftime("%Y-%m-%d")
                if seg_start is None or vf > seg_start:
                    seg_start = vf
            if pd.notna(row["valid_to"]):
                vt = row["valid_to"].strftime("%Y-%m-%d")
                if seg_end is None or vt < seg_end:
                    seg_end = vt

            df = self.get_stock_daily(t, seg_start, seg_end)
            if not df.empty:
                frames.append(df.set_index("date")[field])

        if not frames:
            return pd.Series(dtype="float64")

        # Current ticker is the name
        current = chain.loc[chain["is_current"], "ticker"]
        name = current.iloc[0] if not current.empty else ticker.upper()

        combined = pd.concat(frames).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.name = name
        return combined
