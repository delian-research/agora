"""Structured result type returned by ``agora.download.*`` functions.

A ``DownloadResult`` captures everything an ops dashboard or pipeline
post-mortem would want from a download run: counts, timing, output
location, and the list of tickers (or files, or years) that failed.

Design notes:

- ``slots=True`` so each instance is small.
- ``failed`` and ``warnings`` use ``field(default_factory=list)`` to
  avoid the classic mutable-default footgun.
- ``to_dict()`` returns a JSON-serializable representation suitable for
  ``logger.info("...", extra=result.to_dict())`` or for persisting to
  an external store (e.g. ``data_quality.download_runs`` in delian).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DownloadResult:
    """Outcome of a single ``download_*`` call.

    Attributes:
        stage:           Identifier for the operation (e.g. "download_stocks").
        output_dir:      Directory the function wrote to. ``None`` if no output.
        rows_written:    Total rows written across all output files.
        files_written:   Number of output files written this run.
        bytes_written:   Total bytes across all output files this run.
        duration_seconds: Wall-clock duration of the operation.
        started_at:      UTC timestamp when the operation began.
        finished_at:     UTC timestamp when the operation ended.
        requested:       Total work units requested (e.g. tickers / years).
        completed:       Work units successfully completed this run.
        skipped:         Work units skipped (already in checkpoint).
        failed:          Names of work units that failed (e.g. ticker symbols
                         or year identifiers). Mirrors ``df.attrs[...]``.
        warnings:        Free-form warnings the caller wanted to attach.
        checkpoint_path: The checkpoint file used, if any.
    """

    stage: str
    output_dir: Path | None = None
    rows_written: int = 0
    files_written: int = 0
    bytes_written: int = 0
    duration_seconds: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    requested: int = 0
    completed: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checkpoint_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (Paths → strings, datetimes → ISO)."""
        d = asdict(self)
        if self.output_dir is not None:
            d["output_dir"] = str(self.output_dir)
        if self.checkpoint_path is not None:
            d["checkpoint_path"] = str(self.checkpoint_path)
        if self.started_at is not None:
            d["started_at"] = self.started_at.isoformat()
        if self.finished_at is not None:
            d["finished_at"] = self.finished_at.isoformat()
        return d

    @property
    def succeeded(self) -> bool:
        """True if the run had no failures."""
        return not self.failed
