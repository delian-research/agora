"""Tests for the ``agora.download.cli`` argparse routing.

These cover the CLI dispatch logic by mocking the underlying download
functions and asserting which one was called with which kwargs. No
network, no S3, no actual downloads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agora.download import cli


@pytest.fixture
def mock_downloads(mocker):
    """Mock all four download_X module-level functions.

    The CLI's cmd_X helpers do lazy ``from .stocks import download_stocks``
    inside the function body, so the patch target is the *source* module
    where the symbol lives.
    """
    return {
        "stocks": mocker.patch("agora.download.stocks.download_stocks"),
        "forex": mocker.patch("agora.download.forex.download_forex"),
        "reference": mocker.patch("agora.download.reference.download_reference"),
        "events": mocker.patch("agora.download.reference.download_ticker_events"),
    }


# ── Subcommand dispatch ─────────────────────────────────────────────


class TestSubcommandRouting:
    def test_stocks_routes_to_download_stocks(self, mock_downloads) -> None:
        rc = cli.main(["stocks", "--start-year", "2024", "--end-year", "2024"])
        assert rc == 0
        mock_downloads["stocks"].assert_called_once()
        assert mock_downloads["forex"].call_count == 0

        kwargs = mock_downloads["stocks"].call_args.kwargs
        assert kwargs["start_year"] == 2024
        assert kwargs["end_year"] == 2024
        # resume defaults to True (no --no-resume).
        assert kwargs["resume"] is True

    def test_forex_routes_to_download_forex(self, mock_downloads) -> None:
        rc = cli.main(["forex"])
        assert rc == 0
        mock_downloads["forex"].assert_called_once()
        kwargs = mock_downloads["forex"].call_args.kwargs
        assert kwargs["resume"] is True

    def test_reference_routes_to_download_reference(self, mock_downloads) -> None:
        rc = cli.main(["reference"])
        assert rc == 0
        mock_downloads["reference"].assert_called_once()

    def test_events_routes_to_download_ticker_events(self, mock_downloads) -> None:
        rc = cli.main(["events"])
        assert rc == 0
        mock_downloads["events"].assert_called_once()
        assert mock_downloads["events"].call_args.kwargs["resume"] is True

    def test_all_routes_to_every_command(self, mock_downloads) -> None:
        rc = cli.main(["all", "--start-year", "2024", "--end-year", "2024"])
        assert rc == 0
        # Every downloader should have been invoked exactly once.
        for name, mock in mock_downloads.items():
            assert mock.call_count == 1, f"download_{name} not called"


# ── Flags ────────────────────────────────────────────────────────────


class TestFlags:
    """Global flags (-o, --no-resume, -v) are top-level and must precede
    the subcommand on the CLI."""

    def test_no_resume_sets_resume_false(self, mock_downloads) -> None:
        rc = cli.main(["--no-resume", "stocks"])
        assert rc == 0
        kwargs = mock_downloads["stocks"].call_args.kwargs
        assert kwargs["resume"] is False

    def test_output_flag_routes_to_subdir(self, mock_downloads, tmp_path) -> None:
        rc = cli.main(["-o", str(tmp_path), "stocks"])
        assert rc == 0
        kwargs = mock_downloads["stocks"].call_args.kwargs
        assert kwargs["output_dir"] == tmp_path / "stocks" / "daily"

    def test_no_output_passes_none(self, mock_downloads) -> None:
        rc = cli.main(["forex"])
        assert rc == 0
        kwargs = mock_downloads["forex"].call_args.kwargs
        assert kwargs["output_dir"] is None


# ── Help / error paths ──────────────────────────────────────────────


class TestNoCommand:
    def test_no_subcommand_returns_1(
        self, mock_downloads, capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.main([])
        assert rc == 1
        # No download functions should have been called.
        for mock in mock_downloads.values():
            assert mock.call_count == 0
        # Help text was printed.
        captured = capsys.readouterr()
        assert "agora-download" in captured.out


class TestVerboseFlag:
    def test_verbose_does_not_break(self, mock_downloads) -> None:
        # Just exercise the -v path; no easy way to assert the log level
        # globally without polluting other tests, but we can confirm the
        # dispatch still works.
        rc = cli.main(["-v", "forex"])
        assert rc == 0
        mock_downloads["forex"].assert_called_once()


def test_main_with_default_argv_uses_sys_argv(monkeypatch, mock_downloads) -> None:
    """``main()`` with no argv falls back to sys.argv."""
    monkeypatch.setattr("sys.argv", ["agora-download", "forex"])
    rc = cli.main()
    assert rc == 0
    mock_downloads["forex"].assert_called_once()


def test_build_parser_has_all_subcommands() -> None:
    """Sanity: structural assertion on the argparse object."""
    parser = cli._build_parser()
    # parse_args returns Namespace with command attribute set.
    for sub in ["stocks", "forex", "reference", "events", "all"]:
        ns = parser.parse_args([sub])
        assert ns.command == sub
        assert callable(ns.func)


def test_paths_consistency_when_output_set(mock_downloads, tmp_path: Path) -> None:
    """Each subcommand routes its slice of the output dir correctly."""
    cli.main(["-o", str(tmp_path), "forex"])
    assert mock_downloads["forex"].call_args.kwargs["output_dir"] == tmp_path / "forex"

    cli.main(["-o", str(tmp_path), "reference"])
    assert mock_downloads["reference"].call_args.kwargs["output_dir"] == tmp_path / "reference"

    cli.main(["-o", str(tmp_path), "events"])
    assert mock_downloads["events"].call_args.kwargs["output_dir"] == tmp_path / "reference"
