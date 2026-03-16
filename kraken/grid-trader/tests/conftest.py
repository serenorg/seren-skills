from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_NAMES = (
    "agent",
    "grid_manager",
    "logger",
    "pair_selector",
    "position_tracker",
    "seren_client",
    "serendb_store",
)


def _prepare_local_imports() -> None:
    script_dir = str(SCRIPT_DIR)
    sys.path[:] = [script_dir, *[path for path in sys.path if path != script_dir]]
    for module_name in MODULE_NAMES:
        sys.modules.pop(module_name, None)


_prepare_local_imports()


def pytest_pycollect_makemodule(module_path, parent):
    del module_path, parent
    _prepare_local_imports()
    return None
