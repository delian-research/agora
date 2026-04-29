"""Pytest fixtures + path setup.

We don't install the package by default in this scaffold, so tests run
against the source tree by ensuring the repo root is on ``sys.path``.
A future ``pip install -e .`` (now possible thanks to the build-system
addition in pyproject.toml) makes this redundant, but it costs nothing
to keep for ad-hoc ``pytest`` invocations.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
