"""Issue #468: SKILL.md must mandate a validated first run before scheduling cron.

The `For Claude` block previously instructed Claude to run `setup` and then
immediately call `setup_cron.py create`, scheduling a 6h cron and a 30s
local-pull poller before any evidence the runner can produce qualifying
markets. This test locks the contract: the validation step
(`agent.py --command run --json-output`) must appear *between* setup and
cron creation, and the cron-scheduling step must be gated on the first
run's `status`.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _for_claude_block() -> str:
    text = SKILL_MD.read_text(encoding="utf-8")
    match = re.search(
        r"## For Claude: How to Use This Skill\s*(.+?)(?=\n## )",
        text,
        re.DOTALL,
    )
    assert match, "SKILL.md is missing the `For Claude` section"
    return match.group(1)


def test_for_claude_block_mandates_first_run_validation() -> None:
    block = _for_claude_block()
    assert "--command setup" in block, (
        "For Claude block must reference `agent.py --command setup`"
    )
    assert "--command run" in block and "--json-output" in block, (
        "For Claude block must require `agent.py --command run --json-output` "
        "as a first-run validation step (issue #468)"
    )


def test_for_claude_block_orders_validation_before_cron() -> None:
    block = _for_claude_block()
    setup_idx = block.find("--command setup")
    run_idx = block.find("--command run")
    cron_idx = block.find("setup_cron.py create")
    assert setup_idx != -1 and run_idx != -1 and cron_idx != -1, (
        "For Claude block must reference setup, run, and setup_cron.py create"
    )
    assert setup_idx < run_idx < cron_idx, (
        "For Claude block must order steps as setup -> run --json-output -> "
        "setup_cron.py create (issue #468)"
    )


def test_for_claude_block_gates_cron_on_run_status() -> None:
    block = _for_claude_block()
    has_status_gate = "status=ok" in block or "status: ok" in block
    assert has_status_gate, (
        "For Claude block must gate cron scheduling on the first run "
        "returning `status=ok` (issue #468 acceptance criterion)"
    )
