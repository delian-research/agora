"""CLI entrypoint for downloading historical market data."""

import argparse
import logging
import sys
from pathlib import Path

from .config import DATA_DIR


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_stocks(args):
    from .stocks import download_stocks
    download_stocks(
        output_dir=Path(args.output) / "stocks" / "daily" if args.output else None,
        start_year=args.start_year,
        end_year=args.end_year,
        resume=not args.no_resume,
    )


def cmd_forex(args):
    from .forex import download_forex
    download_forex(
        output_dir=Path(args.output) / "forex" if args.output else None,
        resume=not args.no_resume,
    )


def cmd_reference(args):
    from .reference import download_reference
    download_reference(
        output_dir=Path(args.output) / "reference" if args.output else None,
    )


def cmd_events(args):
    from .reference import download_ticker_events
    download_ticker_events(
        output_dir=Path(args.output) / "reference" if args.output else None,
        resume=not args.no_resume,
    )


def cmd_all(args):
    cmd_stocks(args)
    cmd_forex(args)
    cmd_reference(args)
    cmd_events(args)


def main():
    parser = argparse.ArgumentParser(
        prog="agora-download",
        description="Download historical market data from Massive/Polygon flat files and REST API.",
    )
    parser.add_argument("-o", "--output", type=str, default=None, help="Output directory (default: ./data)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--no-resume", action="store_true", help="Ignore checkpoints, re-download everything")

    subparsers = parser.add_subparsers(dest="command")

    # stocks
    sp = subparsers.add_parser("stocks", help="Download US stock daily OHLCV via S3 flat files")
    sp.add_argument("--start-year", type=int, default=2021, help="Start year (default: 2021)")
    sp.add_argument("--end-year", type=int, default=2026, help="End year (default: 2026)")
    sp.set_defaults(func=cmd_stocks)

    # forex
    sp = subparsers.add_parser("forex", help="Download forex XXX→USD daily OHLCV via REST API")
    sp.set_defaults(func=cmd_forex)

    # reference
    sp = subparsers.add_parser("reference", help="Download reference data (tickers, exchanges, splits, dividends)")
    sp.set_defaults(func=cmd_reference)

    # events
    sp = subparsers.add_parser("events", help="Download ticker events → security master (valid_from/valid_to)")
    sp.set_defaults(func=cmd_events)

    # all
    sp = subparsers.add_parser("all", help="Download everything")
    sp.add_argument("--start-year", type=int, default=2021)
    sp.add_argument("--end-year", type=int, default=2026)
    sp.set_defaults(func=cmd_all)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
