"""Context-manager helper for download timing + counting.

Usage::

    from agora.download.metrics import download_metrics

    def download_stocks(...) -> DownloadResult:
        with download_metrics("download_stocks", output_dir=path) as m:
            ...
            m.rows_written += len(df)
            m.files_written += 1
            m.bytes_written += parquet_path.stat().st_size
            m.completed += 1
            m.failed.append(ticker)  # if applicable
        return m.result

The CM populates ``started_at``, ``finished_at``, and
``duration_seconds`` automatically. Caller mutates the other fields.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from agora.download.result import DownloadResult

logger = logging.getLogger(__name__)


class _MetricsScope:
    """Mutable accumulator handed to the body of ``download_metrics``.

    Exposes the same field names as ``DownloadResult`` so the caller
    can write ``m.rows_written += N`` etc. without having to know the
    eventual return shape.
    """

    __slots__ = (
        "_started_perf",
        "result",
    )

    def __init__(self, result: DownloadResult) -> None:
        self._started_perf = perf_counter()
        self.result = result

    # ── Pass-through attribute access onto the inner DownloadResult ──

    def __getattr__(self, name: str):
        return getattr(self.result, name)

    def __setattr__(self, name: str, value) -> None:
        if name in ("_started_perf", "result"):
            super().__setattr__(name, value)
        else:
            setattr(self.result, name, value)


@contextmanager
def download_metrics(
    stage: str,
    *,
    output_dir: Path | None = None,
    checkpoint_path: Path | None = None,
    log_summary: bool = True,
) -> Iterator[_MetricsScope]:
    """Context manager that wraps a download operation with timing/metrics.

    Yields an accumulator with the same field names as
    :class:`DownloadResult`. On ``__exit__`` it stamps the timing
    fields and (by default) emits one structured ``logger.info``
    summary line with the full result as ``extra=`` so cron logs are
    greppable for ops.
    """
    started_at = datetime.now(UTC)
    result = DownloadResult(
        stage=stage,
        output_dir=output_dir,
        checkpoint_path=checkpoint_path,
        started_at=started_at,
    )
    scope = _MetricsScope(result)

    try:
        yield scope
    finally:
        finished_at = datetime.now(UTC)
        scope.result.finished_at = finished_at
        scope.result.duration_seconds = round(perf_counter() - scope._started_perf, 3)

        if log_summary:
            logger.info(
                "%s summary: requested=%d, completed=%d, skipped=%d, failed=%d, "
                "rows=%d, files=%d, bytes=%d, duration=%.3fs",
                stage,
                scope.result.requested, scope.result.completed,
                scope.result.skipped, len(scope.result.failed),
                scope.result.rows_written, scope.result.files_written,
                scope.result.bytes_written, scope.result.duration_seconds,
                extra=scope.result.to_dict(),
            )
