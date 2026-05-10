"""Tests for ``agora.download.checkpoint.Checkpoint``.

Covers the resume-tracker contract: empty start, mark/is_done roundtrip,
disk persistence + reload, and reset.
"""

from __future__ import annotations

from pathlib import Path

from agora.download.checkpoint import Checkpoint


class TestEmptyCheckpoint:
    def test_no_file_starts_empty(self, tmp_path: Path) -> None:
        ckpt = Checkpoint(tmp_path / ".checkpoint.json")
        assert ckpt.completed_count == 0
        assert ckpt.is_done("anything") is False

    def test_no_file_is_not_created_on_construction(self, tmp_path: Path) -> None:
        path = tmp_path / ".checkpoint.json"
        Checkpoint(path)
        # Reading a non-existent checkpoint should not write anything.
        assert not path.exists()


class TestMarkAndQuery:
    def test_mark_done_makes_is_done_true(self, tmp_path: Path) -> None:
        ckpt = Checkpoint(tmp_path / ".checkpoint.json")
        ckpt.mark_done("AAPL")
        assert ckpt.is_done("AAPL") is True
        assert ckpt.is_done("MSFT") is False

    def test_mark_done_increments_completed_count(self, tmp_path: Path) -> None:
        ckpt = Checkpoint(tmp_path / ".checkpoint.json")
        for ticker in ("AAPL", "MSFT", "NVDA"):
            ckpt.mark_done(ticker)
        assert ckpt.completed_count == 3

    def test_mark_done_is_idempotent(self, tmp_path: Path) -> None:
        ckpt = Checkpoint(tmp_path / ".checkpoint.json")
        ckpt.mark_done("AAPL")
        ckpt.mark_done("AAPL")
        ckpt.mark_done("AAPL")
        assert ckpt.completed_count == 1


class TestDiskPersistence:
    def test_mark_done_writes_to_disk(self, tmp_path: Path) -> None:
        path = tmp_path / ".checkpoint.json"
        ckpt = Checkpoint(path)
        ckpt.mark_done("AAPL")
        assert path.exists()
        # File contains the completed list as JSON.
        import json
        data = json.loads(path.read_text())
        assert "AAPL" in data["completed"]

    def test_state_round_trips_via_disk(self, tmp_path: Path) -> None:
        path = tmp_path / ".checkpoint.json"
        first = Checkpoint(path)
        for ticker in ("AAPL", "MSFT", "NVDA"):
            first.mark_done(ticker)

        # Fresh instance reads the same file.
        second = Checkpoint(path)
        assert second.completed_count == 3
        assert second.is_done("AAPL")
        assert second.is_done("MSFT")
        assert second.is_done("NVDA")
        assert not second.is_done("GOOGL")


class TestReset:
    def test_reset_clears_state_and_removes_file(self, tmp_path: Path) -> None:
        path = tmp_path / ".checkpoint.json"
        ckpt = Checkpoint(path)
        ckpt.mark_done("AAPL")
        ckpt.mark_done("MSFT")

        ckpt.reset()
        assert ckpt.completed_count == 0
        assert ckpt.is_done("AAPL") is False
        assert not path.exists()

    def test_reset_is_safe_when_file_missing(self, tmp_path: Path) -> None:
        path = tmp_path / ".checkpoint.json"
        ckpt = Checkpoint(path)
        # No mark_done calls, so no file created. reset() must not raise.
        ckpt.reset()
        assert ckpt.completed_count == 0


class TestParentDirCreation:
    def test_creates_parent_dirs_on_save(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c" / ".checkpoint.json"
        ckpt = Checkpoint(nested)
        ckpt.mark_done("X")
        assert nested.exists()
