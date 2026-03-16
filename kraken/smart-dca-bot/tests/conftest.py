from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
MODULE_NAMES = (
    "agent",
    "dca_engine",
    "logger",
    "optimizer",
    "portfolio_manager",
    "position_tracker",
    "scanner",
    "seren_api_client",
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


@pytest.fixture(autouse=True)
def _stub_seren_api_key(monkeypatch):
    monkeypatch.setenv("SEREN_API_KEY", os.getenv("SEREN_API_KEY", "sb_local_test"))
    agent = importlib.import_module("agent")
    monkeypatch.setattr(
        agent,
        "ensure_seren_api_key",
        lambda config: os.getenv("SEREN_API_KEY", "sb_local_test"),
    )
