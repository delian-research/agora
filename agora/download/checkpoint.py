"""Simple JSON-based checkpoint for resumable downloads."""

import json
from pathlib import Path


class Checkpoint:
    """Track completed downloads so interrupted runs can resume."""

    def __init__(self, path: Path):
        self._path = path
        self._completed: set[str] = set()
        self._load()

    def _load(self):
        if self._path.exists():
            data = json.loads(self._path.read_text())
            self._completed = set(data.get("completed", []))

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"completed": sorted(self._completed)}, indent=2))

    def is_done(self, key: str) -> bool:
        return key in self._completed

    def mark_done(self, key: str):
        self._completed.add(key)
        self._save()

    @property
    def completed_count(self) -> int:
        return len(self._completed)

    def reset(self):
        self._completed.clear()
        if self._path.exists():
            self._path.unlink()