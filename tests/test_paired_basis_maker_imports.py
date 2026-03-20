from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TARGETS = [
    (
        "high-throughput-paired-basis-maker",
        REPO_ROOT / "polymarket" / "high-throughput-paired-basis-maker" / "scripts",
    ),
    (
        "liquidity-paired-basis-maker",
        REPO_ROOT / "polymarket" / "liquidity-paired-basis-maker" / "scripts",
    ),
]


@pytest.mark.parametrize("skill_slug,script_dir", TARGETS, ids=[slug for slug, _ in TARGETS])
def test_paired_basis_maker_agent_imports_with_bundled_replay_module(
    skill_slug: str,
    script_dir: Path,
) -> None:
    agent_path = script_dir / "agent.py"
    replay_path = script_dir / "pair_stateful_replay.py"

    assert replay_path.exists(), f"{skill_slug} is missing {replay_path.name}"

    spec = importlib.util.spec_from_file_location(f"{skill_slug.replace('-', '_')}_agent_test", agent_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    script_dir_str = str(script_dir)
    original_sys_path = list(sys.path)

    try:
        if script_dir_str not in sys.path:
            sys.path.insert(0, script_dir_str)
        sys.modules.pop("pair_stateful_replay", None)
        sys.modules.pop("polymarket_live", None)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_sys_path
        sys.modules.pop(spec.name, None)
        sys.modules.pop("pair_stateful_replay", None)
        sys.modules.pop("polymarket_live", None)
