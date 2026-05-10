"""Tests for ``DownloadResult`` and the ``download_metrics`` context manager."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pytest

from agora.download.metrics import download_metrics
from agora.download.result import DownloadResult

# ── DownloadResult ──────────────────────────────────────────────────


class TestDownloadResultDefaults:
    def test_minimal_construction(self) -> None:
        r = DownloadResult(stage="test")
        assert r.stage == "test"
        assert r.rows_written == 0
        assert r.failed == []
        assert r.warnings == []
        assert r.succeeded is True

    def test_failed_makes_succeeded_false(self) -> None:
        r = DownloadResult(stage="test", failed=["AAPL"])
        assert r.succeeded is False

    def test_default_factory_avoids_shared_mutable(self) -> None:
        """Two instances must not share the same failed/warnings list."""
        a = DownloadResult(stage="a")
        b = DownloadResult(stage="b")
        a.failed.append("AAPL")
        assert b.failed == []


class TestDownloadResultToDict:
    def test_paths_stringified(self, tmp_path: Path) -> None:
        r = DownloadResult(
            stage="x", output_dir=tmp_path, checkpoint_path=tmp_path / ".ckpt",
        )
        d = r.to_dict()
        assert isinstance(d["output_dir"], str)
        assert isinstance(d["checkpoint_path"], str)
        assert d["output_dir"] == str(tmp_path)

    def test_datetimes_isoformat(self) -> None:
        now = datetime(2025, 1, 1, 12, 30, 45)
        r = DownloadResult(stage="x", started_at=now, finished_at=now)
        d = r.to_dict()
        assert d["started_at"] == "2025-01-01T12:30:45"
        assert d["finished_at"] == "2025-01-01T12:30:45"

    def test_none_paths_stay_none(self) -> None:
        r = DownloadResult(stage="x")
        d = r.to_dict()
        assert d["output_dir"] is None
        assert d["checkpoint_path"] is None
        assert d["started_at"] is None


# ── download_metrics context manager ────────────────────────────────


class TestDownloadMetrics:
    def test_happy_path_sets_timing(self) -> None:
        with download_metrics("test_stage") as m:
            m.rows_written = 100
            m.files_written = 1
            m.completed = 1

        result = m.result
        assert result.stage == "test_stage"
        assert result.rows_written == 100
        assert result.files_written == 1
        assert result.completed == 1
        assert result.started_at is not None
        assert result.finished_at is not None
        assert result.finished_at >= result.started_at
        assert result.duration_seconds >= 0.0

    def test_duration_reflects_elapsed_time(self) -> None:
        with download_metrics("sleep_test", log_summary=False) as m:
            time.sleep(0.05)
            m.completed = 1
        # ~50ms; allow generous slop for CI scheduling.
        assert m.result.duration_seconds >= 0.04

    def test_logs_summary_by_default(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="agora.download.metrics"):
            with download_metrics("logged_stage") as m:
                m.rows_written = 5
                m.completed = 1

        records = [r for r in caplog.records if "logged_stage" in r.getMessage()]
        assert records, "expected a summary log line"
        rec = records[-1]
        # Structured extra is attached to the LogRecord (logging machinery
        # promotes extra keys to attributes).
        assert getattr(rec, "stage", None) == "logged_stage"
        assert getattr(rec, "rows_written", None) == 5

    def test_log_summary_can_be_disabled(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="agora.download.metrics"):
            with download_metrics("silent_stage", log_summary=False):
                pass
        records = [r for r in caplog.records if "silent_stage summary" in r.getMessage()]
        assert not records

    def test_exceptions_still_record_timing(self) -> None:
        with pytest.raises(RuntimeError):
            with download_metrics("error_stage", log_summary=False) as m:
                m.completed = 1
                raise RuntimeError("boom")

        # On exception the result is still updated.
        assert m.result.duration_seconds >= 0.0
        assert m.result.finished_at is not None
        assert m.result.completed == 1

    def test_failed_list_accumulates(self) -> None:
        with download_metrics("fail_stage", log_summary=False) as m:
            m.failed.append("AAPL")
            m.failed.append("MSFT")
        assert m.result.failed == ["AAPL", "MSFT"]
        assert m.result.succeeded is False

    def test_output_dir_threaded_through(self, tmp_path: Path) -> None:
        with download_metrics(
            "with_dir", output_dir=tmp_path, log_summary=False,
        ) as m:
            pass
        assert m.result.output_dir == tmp_path
