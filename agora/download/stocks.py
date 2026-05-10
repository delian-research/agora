"""Download US stock daily OHLCV from S3 flat files → yearly Parquet."""

import gzip
import io
import logging
from pathlib import Path

import pandas as pd
from botocore.exceptions import ClientError

from .checkpoint import Checkpoint
from .config import DATA_DIR, S3_BUCKET, get_s3_client

logger = logging.getLogger(__name__)

S3_PREFIX = "us_stocks_sip/day_aggs_v1"

# Columns in the flat file CSV
CSV_COLUMNS = ["ticker", "volume", "open", "close", "high", "low", "window_start", "transactions"]


def _list_available_files(s3, year: int) -> list[dict]:
    """List all flat file keys for a given year."""
    paginator = s3.get_paginator("list_objects_v2")
    files = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/{year}/"):
        for obj in page.get("Contents", []):
            files.append({"key": obj["Key"], "size": obj["Size"]})
    return files


def _download_and_parse(s3, key: str) -> pd.DataFrame:
    """Download a single CSV.gz from S3 and parse into a DataFrame."""
    obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    raw = obj["Body"].read()
    text = gzip.decompress(raw).decode("utf-8")
    df = pd.read_csv(io.StringIO(text))
    # Convert window_start (nanosecond epoch) to date
    df["date"] = pd.to_datetime(df["window_start"], unit="ns", utc=True).dt.date
    return df


def download_stocks(
    output_dir: Path | None = None,
    start_year: int = 2021,
    end_year: int = 2026,
    resume: bool = True,
) -> Path:
    """Download stock daily OHLCV flat files and save as yearly Parquet.

    Args:
        output_dir: Directory to write Parquet files. Defaults to data/stocks/daily/.
        start_year: First year to download (inclusive). Files before May 2021 are blocked.
        end_year: Last year to download (inclusive).
        resume: If True, skip files already downloaded (checkpoint-based).

    Returns:
        Path to the output directory containing yearly Parquet files.
    """
    output_dir = output_dir or DATA_DIR / "stocks" / "daily"
    output_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = Checkpoint(output_dir / ".checkpoint.json")
    s3 = get_s3_client()

    years_completed: list[int] = []
    years_skipped: list[int] = []
    years_empty: list[int] = []

    for year in range(start_year, end_year + 1):
        parquet_path = output_dir / f"{year}.parquet"

        # Skip if this year is already fully downloaded
        year_key = f"year:{year}"
        if resume and checkpoint.is_done(year_key):
            logger.info(f"Skipping {year} (already complete)")
            years_skipped.append(year)
            continue

        logger.info(f"Downloading {year}...")
        files = _list_available_files(s3, year)

        if not files:
            logger.warning(f"No files found for {year}")
            years_empty.append(year)
            continue

        year_frames = []
        downloaded = 0
        skipped = 0

        for file_info in files:
            key = file_info["key"]

            try:
                df = _download_and_parse(s3, key)
                year_frames.append(df)
                downloaded += 1

                if downloaded % 50 == 0:
                    logger.info(f"  {year}: {downloaded}/{len(files)} files downloaded")

            except ClientError as e:
                if e.response["Error"]["Code"] == "403":
                    skipped += 1
                    continue
                raise

        if not year_frames:
            logger.warning(f"No accessible data for {year} (skipped {skipped} files)")
            years_empty.append(year)
            continue

        # Combine all days for this year into a single DataFrame
        combined = pd.concat(year_frames, ignore_index=True)

        # Clean up and optimize dtypes
        combined = combined.rename(columns={"transactions": "trades"})
        combined["date"] = pd.to_datetime(combined["date"])
        combined = combined[["date", "ticker", "open", "high", "low", "close", "volume", "trades"]].copy()
        combined = combined.sort_values(["date", "ticker"]).reset_index(drop=True)

        # Write Parquet
        combined.to_parquet(parquet_path, index=False, engine="pyarrow")

        size_mb = parquet_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"  {year}: Wrote {parquet_path.name} "
            f"({len(combined)} rows, {combined['ticker'].nunique()} tickers, "
            f"{downloaded} days, {size_mb:.1f} MB)"
        )

        checkpoint.mark_done(year_key)
        years_completed.append(year)

    requested = list(range(start_year, end_year + 1))
    logger.info(
        "download_stocks summary: requested=%d, downloaded=%d, skipped=%d, empty=%d",
        len(requested), len(years_completed), len(years_skipped), len(years_empty),
        extra={
            "stage": "download_stocks.summary",
            "requested": requested,
            "downloaded": years_completed,
            "skipped": years_skipped,
            "empty": years_empty,
        },
    )

    return output_dir
