"""CLI entrypoint for downloading historical market data."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path


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


def cmd_security_master(args):
    from .security_master import sync_security_master
    sync_security_master(
        output_dir=Path(args.output) / "reference" if args.output else None,
        backfill_events=not args.no_events,
        full_event_backfill=args.full_event_backfill,
        write_dated_snapshot=not args.no_snapshot,
        strict_universe=not args.allow_partial,
    )


def cmd_all(args):
    cmd_stocks(args)
    cmd_forex(args)
    cmd_reference(args)
    cmd_events(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agora-download",
        description="Download historical market data from Massive/Polygon flat files and REST API.",
    )
    parser.add_argument("-o", "--output", type=str, default=None, help="Output directory (default: ./data)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--no-resume", action="store_true", help="Ignore checkpoints, re-download everything")

    subparsers = parser.add_subparsers(dest="command")

    sp = subparsers.add_parser("stocks", help="Download US stock daily OHLCV via S3 flat files")
    sp.add_argument("--start-year", type=int, default=2021, help="Start year (default: 2021)")
    sp.add_argument("--end-year", type=int, default=2026, help="End year (default: 2026)")
    sp.set_defaults(func=cmd_stocks)

    sp = subparsers.add_parser("forex", help="Download forex XXX→USD daily OHLCV via REST API")
    sp.set_defaults(func=cmd_forex)

    sp = subparsers.add_parser("reference", help="Download reference data (tickers, exchanges, splits, dividends)")
    sp.set_defaults(func=cmd_reference)

    sp = subparsers.add_parser("events", help="Download ticker events → security master (valid_from/valid_to)")
    sp.set_defaults(func=cmd_events)

    sp = subparsers.add_parser(
        "security-master",
        help="Sync security master + append timestamped change log",
    )
    sp.add_argument(
        "--no-events",
        action="store_true",
        help="Skip ticker_events backfill on additions / detected changes",
    )
    sp.add_argument(
        "--full-event-backfill",
        action="store_true",
        help=(
            "One-shot: pull get_ticker_events for every identity in the master, "
            "not just changed ones. Idempotent against the existing change log. "
            "~10K API calls — slow."
        ),
    )
    sp.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Skip writing the dated raw-pull snapshot under reference/snapshots/",
    )
    sp.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Continue when one or more (market, type) list_tickers calls fail. "
            "Default is to abort the sync on any failure to prevent the diff "
            "layer from emitting spurious deactivation rows for missing segments."
        ),
    )
    sp.set_defaults(func=cmd_security_master)

    sp = subparsers.add_parser("all", help="Download everything")
    sp.add_argument("--start-year", type=int, default=2021)
    sp.add_argument("--end-year", type=int, default=2026)
    sp.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns the process exit code.

    Args:
        argv: Argument list (excludes program name). When ``None`` (the
            default), parses ``sys.argv``. Tests pass an explicit list.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return 1

    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
