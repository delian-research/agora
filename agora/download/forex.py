"""Download forex (XXX→USD) daily OHLCV via REST API → Parquet."""

import datetime
import logging
import time
from pathlib import Path

import pandas as pd
from massive import RESTClient
from massive.exceptions import BadResponse

from .checkpoint import Checkpoint
from .config import DATA_DIR, REST_RATE_LIMIT

logger = logging.getLogger(__name__)

# Rate limit: 5 calls per minute → 12 seconds between calls
CALL_INTERVAL = 60.0 / REST_RATE_LIMIT


def _get_usd_pairs(client: RESTClient) -> list[str]:
    """Get all active forex tickers ending in USD."""
    tickers = list(client.list_tickers(market="fx", active=True, limit=1000))
    return sorted(t.ticker for t in tickers if t.ticker.endswith("USD"))


def download_forex(
    output_dir: Path | None = None,
    api_key: str | None = None,
    years_back: int = 2,
    resume: bool = True,
) -> Path:
    """Download daily OHLCV for all foreign-currency-to-USD pairs.

    Args:
        output_dir: Directory to write Parquet file. Defaults to data/forex/.
        api_key: Massive/Polygon API key. Uses env var if not provided.
        years_back: How many years of history to fetch (default 2).
        resume: If True, skip tickers already downloaded.

    Returns:
        Path to the output Parquet file.
    """
    import os

    output_dir = output_dir or DATA_DIR / "forex"
    output_dir.mkdir(parents=True, exist_ok=True)

    parquet_path = output_dir / "daily_usd.parquet"
    checkpoint = Checkpoint(output_dir / ".checkpoint.json")

    key = api_key or os.getenv("MASSIVE_API_KEY")
    client = RESTClient(api_key=key)

    # Date range
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=years_back * 365)

    # Get ticker list
    pairs = _get_usd_pairs(client)
    logger.info(f"Found {len(pairs)} USD pairs to download")
    logger.info(f"Date range: {start_date} → {end_date}")
    logger.info(f"Rate limit: {REST_RATE_LIMIT} calls/min ({CALL_INTERVAL:.0f}s between calls)")

    all_frames = []

    # Load any existing data to append to
    if resume and parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        all_frames.append(existing)
        logger.info(f"Loaded existing data: {len(existing)} rows")

    completed = 0
    skipped = 0

    for i, ticker in enumerate(pairs):
        if resume and checkpoint.is_done(ticker):
            skipped += 1
            continue

        # Rate limiting
        if completed > 0:
            time.sleep(CALL_INTERVAL)

        try:
            aggs = list(client.list_aggs(
                ticker=ticker,
                multiplier=1,
                timespan="day",
                from_=start_date.isoformat(),
                to=end_date.isoformat(),
                adjusted=True,
                sort="asc",
                limit=50000,
            ))

            if aggs:
                rows = []
                for a in aggs:
                    rows.append({
                        "date": pd.to_datetime(a.timestamp, unit="ms", utc=True).date(),
                        "ticker": ticker,
                        "open": a.open,
                        "high": a.high,
                        "low": a.low,
                        "close": a.close,
                        "volume": a.volume,
                        "trades": getattr(a, "transactions", None),
                    })
                df = pd.DataFrame(rows)
                df["date"] = pd.to_datetime(df["date"])
                all_frames.append(df)

            completed += 1
            checkpoint.mark_done(ticker)

            if completed % 10 == 0:
                logger.info(
                    f"  Progress: {completed + skipped}/{len(pairs)} "
                    f"({completed} downloaded, {skipped} skipped)"
                )

        except BadResponse as e:
            if "NOT_AUTHORIZED" in str(e):
                logger.warning(f"  {ticker}: Not authorized, skipping")
                checkpoint.mark_done(ticker)
                skipped += 1
            else:
                logger.error(f"  {ticker}: API error: {e}")
                # Don't mark as done — will retry on next run

    # Combine and write
    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"])
        combined = combined.sort_values(["date", "ticker"]).reset_index(drop=True)
        combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
        combined.to_parquet(parquet_path, index=False, engine="pyarrow")

        size_mb = parquet_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"Done: Wrote {parquet_path.name} "
            f"({len(combined)} rows, {combined['ticker'].nunique()} pairs, {size_mb:.1f} MB)"
        )

    return parquet_path
