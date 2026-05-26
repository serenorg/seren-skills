"""Lint smoke: `scripts/` must pass `ruff check`.

This guards the whole module tree against the failure class behind
#833 (F821 undefined name) and #835 (F401 unused import). A single
process-level check is enough — per-file unit tests would just
duplicate ruff's own rule coverage.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"


def test_scripts_dir_passes_ruff_check():
    ruff = shutil.which("ruff") or str(SKILL_ROOT / ".venv" / "bin" / "ruff")
    if not Path(ruff).exists():
        pytest.skip("ruff binary not available in this environment")
    result = subprocess.run(
        [ruff, "check", str(SCRIPTS_DIR)],
        cwd=SKILL_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ruff check failed for scripts/:\n{result.stdout}{result.stderr}"
    )
