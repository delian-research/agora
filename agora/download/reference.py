"""Download reference data (tickers, exchanges, events, splits, dividends) → Parquet."""

import logging
import os
import time
from pathlib import Path

import pandas as pd
from massive import RESTClient
from massive.exceptions import BadResponse

from .checkpoint import Checkpoint
from .config import DATA_DIR, REST_RATE_LIMIT

logger = logging.getLogger(__name__)

CALL_INTERVAL = 60.0 / REST_RATE_LIMIT


def _download_tickers(client: RESTClient) -> pd.DataFrame:
    """Download all active stock + forex ticker reference data."""
    logger.info("Downloading ticker reference data...")
    all_tickers = []

    for market in ("stocks", "fx"):
        tickers = list(client.list_tickers(market=market, active=True, limit=1000))
        for t in tickers:
            all_tickers.append({
                "ticker": t.ticker,
                "name": t.name,
                "market": t.market,
                "type": t.type,
                "locale": t.locale,
                "active": t.active,
                "currency_name": getattr(t, "currency_name", None),
                "composite_figi": getattr(t, "composite_figi", None),
                "share_class_figi": getattr(t, "share_class_figi", None),
                "cik": getattr(t, "cik", None),
                "primary_exchange": getattr(t, "primary_exchange", None),
                "last_updated_utc": getattr(t, "last_updated_utc", None),
                "source_feed": getattr(t, "source_feed", None),
            })

    df = pd.DataFrame(all_tickers)
    logger.info(f"  Got {len(df)} tickers ({df['market'].value_counts().to_dict()})")
    return df


def _download_exchanges(client: RESTClient) -> pd.DataFrame:
    """Download exchange reference data."""
    logger.info("Downloading exchanges...")
    exchanges = client.get_exchanges()
    if not exchanges:
        return pd.DataFrame()

    rows = []
    for e in exchanges:
        rows.append({
            "id": e.id,
            "mic": getattr(e, "mic", None),
            "operating_mic": getattr(e, "operating_mic", None),
            "name": e.name,
            "type": e.type,
            "asset_class": getattr(e, "asset_class", None),
            "locale": getattr(e, "locale", None),
            "acronym": getattr(e, "acronym", None),
            "participant_id": getattr(e, "participant_id", None),
            "url": getattr(e, "url", None),
        })

    df = pd.DataFrame(rows)
    logger.info(f"  Got {len(df)} exchanges")
    return df


def _download_ticker_events(
    client: RESTClient,
    tickers_df: pd.DataFrame,
    checkpoint: Checkpoint,
    ticker_types: tuple[str, ...] = ("CS", "ETF"),
    resume: bool = True,
) -> pd.DataFrame:
    """Download ticker events to build security master with valid_from/valid_to.

    Fetches the event history for each ticker (symbol changes, etc.)
    and transforms the chain into a security master with date ranges.
    """
    # Filter to requested ticker types
    mask = (tickers_df["market"] == "stocks") & (tickers_df["type"].isin(ticker_types))
    target_tickers = sorted(tickers_df.loc[mask, "ticker"].tolist())
    logger.info(
        f"Downloading ticker events for {len(target_tickers)} tickers "
        f"(types: {ticker_types})"
    )

    rows = []
    completed = 0
    skipped = 0
    errors = 0

    for ticker in target_tickers:
        ckpt_key = f"event:{ticker}"
        if resume and checkpoint.is_done(ckpt_key):
            skipped += 1
            continue

        try:
            result = client.get_ticker_events(ticker)
            events = result.events or []
            composite_figi = getattr(result, "composite_figi", None)
            security_name = getattr(result, "name", None)
            cik = getattr(result, "cik", None)

            # Build the valid_from / valid_to chain from events
            # Events are ordered newest-first: [{ticker: META, date: 2022-06-09}, {ticker: FB, date: 2012-05-18}]
            ticker_changes = [
                e for e in events if e.get("type") == "ticker_change"
            ]

            if ticker_changes:
                for j, ev in enumerate(ticker_changes):
                    tc = ev.get("ticker_change", {})
                    valid_from = ev.get("date")
                    # valid_to is the day before the next (newer) event, or None if current
                    valid_to = ticker_changes[j - 1].get("date") if j > 0 else None

                    rows.append({
                        "ticker": tc.get("ticker"),
                        "composite_figi": composite_figi,
                        "cik": cik,
                        "name": security_name,
                        "valid_from": valid_from,
                        "valid_to": valid_to,
                        "is_current": j == 0,
                    })
            else:
                # No events — ticker has never changed
                rows.append({
                    "ticker": ticker,
                    "composite_figi": composite_figi,
                    "cik": cik,
                    "name": security_name,
                    "valid_from": None,
                    "valid_to": None,
                    "is_current": True,
                })

            completed += 1
            checkpoint.mark_done(ckpt_key)

            if (completed + skipped) % 100 == 0:
                logger.info(
                    f"  Events progress: {completed + skipped}/{len(target_tickers)} "
                    f"({completed} downloaded, {skipped} skipped, {errors} errors)"
                )

        except BadResponse as e:
            if "NOT_AUTHORIZED" in str(e) or "NOT_FOUND" in str(e):
                checkpoint.mark_done(ckpt_key)
                skipped += 1
            else:
                errors += 1
                logger.warning(f"  {ticker}: API error: {e}")

        except Exception as e:
            errors += 1
            logger.warning(f"  {ticker}: Unexpected error: {e}")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["valid_from"] = pd.to_datetime(df["valid_from"], errors="coerce")
    df["valid_to"] = pd.to_datetime(df["valid_to"], errors="coerce")
    logger.info(
        f"  Got {len(df)} security master records "
        f"({df['composite_figi'].nunique()} unique securities)"
    )
    return df


def _download_splits(client: RESTClient) -> pd.DataFrame:
    """Download all stock splits."""
    logger.info("Downloading stock splits...")
    splits = list(client.list_splits(limit=1000))
    if not splits:
        return pd.DataFrame()

    rows = []
    for s in splits:
        rows.append({
            "ticker": s.ticker,
            "execution_date": s.execution_date,
            "split_from": s.split_from,
            "split_to": s.split_to,
        })

    df = pd.DataFrame(rows)
    df["execution_date"] = pd.to_datetime(df["execution_date"])
    logger.info(f"  Got {len(df)} splits")
    return df


def _download_dividends(client: RESTClient) -> pd.DataFrame:
    """Download all stock dividends."""
    logger.info("Downloading stock dividends...")
    divs = list(client.list_dividends(limit=1000))
    if not divs:
        return pd.DataFrame()

    rows = []
    for d in divs:
        rows.append({
            "ticker": d.ticker,
            "ex_dividend_date": d.ex_dividend_date,
            "pay_date": getattr(d, "pay_date", None),
            "record_date": getattr(d, "record_date", None),
            "declaration_date": getattr(d, "declaration_date", None),
            "cash_amount": d.cash_amount,
            "currency": getattr(d, "currency", None),
            "frequency": getattr(d, "frequency", None),
            "dividend_type": getattr(d, "dividend_type", None),
        })

    df = pd.DataFrame(rows)
    for col in ("ex_dividend_date", "pay_date", "record_date", "declaration_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    logger.info(f"  Got {len(df)} dividends")
    return df


def _get_price_tickers() -> set[str]:
    """Get the set of tickers that appear in our downloaded price data."""
    price_dir = DATA_DIR / "stocks" / "daily"
    tickers = set()
    if price_dir.exists():
        for f in price_dir.glob("*.parquet"):
            df = pd.read_parquet(f, columns=["ticker"])
            tickers.update(df["ticker"].unique())
    return tickers


def download_reference(
    output_dir: Path | None = None,
    api_key: str | None = None,
) -> Path:
    """Download reference data and save as Parquet files.

    Downloads:
        - Ticker details (stocks + forex)
        - Exchanges
        - Stock splits
        - Stock dividends

    Args:
        output_dir: Directory to write Parquet files. Defaults to data/reference/.
        api_key: Massive/Polygon API key. Uses env var if not provided.

    Returns:
        Path to the output directory.
    """
    output_dir = output_dir or DATA_DIR / "reference"
    output_dir.mkdir(parents=True, exist_ok=True)

    key = api_key or os.getenv("MASSIVE_API_KEY")
    client = RESTClient(api_key=key)

    # Tickers
    tickers_df = _download_tickers(client)
    tickers_df.to_parquet(output_dir / "tickers.parquet", index=False, engine="pyarrow")

    time.sleep(CALL_INTERVAL)

    # Exchanges
    exchanges_df = _download_exchanges(client)
    if not exchanges_df.empty:
        exchanges_df.to_parquet(output_dir / "exchanges.parquet", index=False, engine="pyarrow")

    time.sleep(CALL_INTERVAL)

    # Splits
    splits_df = _download_splits(client)
    if not splits_df.empty:
        splits_df.to_parquet(output_dir / "splits.parquet", index=False, engine="pyarrow")

    time.sleep(CALL_INTERVAL)

    # Dividends
    dividends_df = _download_dividends(client)
    if not dividends_df.empty:
        dividends_df.to_parquet(output_dir / "dividends.parquet", index=False, engine="pyarrow")

    logger.info(f"Reference data saved to {output_dir}")
    return output_dir


def download_ticker_events(
    output_dir: Path | None = None,
    api_key: str | None = None,
    ticker_types: tuple[str, ...] = ("CS", "ETF"),
    resume: bool = True,
) -> Path:
    """Download ticker events for tickers in our price data → security master.

    Scoped to tickers that appear in the downloaded stock price data,
    filtered to the requested ticker types (default: CS + ETF).

    Args:
        output_dir: Directory to write Parquet file. Defaults to data/reference/.
        api_key: Massive/Polygon API key. Uses env var if not provided.
        ticker_types: Ticker types to include.
        resume: If True, skip tickers already downloaded.

    Returns:
        Path to the output directory.
    """
    output_dir = output_dir or DATA_DIR / "reference"
    output_dir.mkdir(parents=True, exist_ok=True)

    key = api_key or os.getenv("MASSIVE_API_KEY")
    client = RESTClient(api_key=key)

    # Load reference tickers and scope to price data
    tickers_path = output_dir / "tickers.parquet"
    if not tickers_path.exists():
        logger.info("Tickers not yet downloaded, fetching first...")
        tickers_df = _download_tickers(client)
        tickers_df.to_parquet(tickers_path, index=False, engine="pyarrow")
        time.sleep(CALL_INTERVAL)
    else:
        tickers_df = pd.read_parquet(tickers_path)

    # Scope to tickers in our price data
    price_tickers = _get_price_tickers()
    if price_tickers:
        tickers_df = tickers_df[tickers_df["ticker"].isin(price_tickers)]
        logger.info(f"Scoped to {len(tickers_df)} tickers present in price data")

    checkpoint = Checkpoint(output_dir / ".events_checkpoint.json")

    events_df = _download_ticker_events(
        client, tickers_df, checkpoint,
        ticker_types=ticker_types,
        resume=resume,
    )

    if not events_df.empty:
        parquet_path = output_dir / "ticker_events.parquet"
        # Merge with any existing data if resuming
        if resume and parquet_path.exists():
            existing = pd.read_parquet(parquet_path)
            events_df = pd.concat([existing, events_df], ignore_index=True)
            events_df = events_df.drop_duplicates(
                subset=["ticker", "composite_figi", "valid_from"], keep="last"
            )
        events_df = events_df.sort_values(["composite_figi", "valid_from"]).reset_index(drop=True)
        events_df.to_parquet(parquet_path, index=False, engine="pyarrow")
        logger.info(f"Wrote {parquet_path.name} ({len(events_df)} rows)")

    return output_dir
