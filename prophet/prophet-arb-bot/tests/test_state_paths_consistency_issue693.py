"""Issue #693: state-dir resolution must be consistent across modules.

Symptom: operator tails `<repo>/state/run_progress.jsonl` and sees 0 bytes
for the entire cycle, even while events fire. Root cause: `progress.py`
resolves the state dir to `~/.config/seren/skills/prophet-arb-bot/state/`
(canonical Seren Desktop runtime dir), but
`discovery/candidate_sheet.py` resolves to `<repo>/state/` via
`Path(__file__).parent.parent.parent`. The two writers land in
different directories whenever the agent is run from the source tree
(dev / verification E2E), making the SKILL.md instruction "arm a
Monitor on state/run_progress.jsonl" path-ambiguous.

This test is the load-bearing assertion that the two writers agree.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from progress import ProgressEmitter
from discovery import candidate_sheet


def _candidate_sheet_default_dir() -> Path:
    """Resolve where `candidate_sheet.write_candidate_sheet` would write.

    The function takes an optional `output_dir`; when omitted it derives
    the path internally. We re-derive it the same way the production
    helper does so the test pins the contract, not the implementation.
    """
    return candidate_sheet._default_output_dir()  # type: ignore[attr-defined]


def test_progress_and_candidate_sheet_resolve_to_same_dir_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force a known HOME so the default-path branch is deterministic and
    # we never accidentally write to the operator's real ~/.config.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PROPHET_ARB_STATE_DIR", raising=False)

    emitter_dir = ProgressEmitter().current_path.parent.resolve()
    sheet_dir = _candidate_sheet_default_dir().resolve()

    assert emitter_dir == sheet_dir, (
        f"State-dir split detected (issue #693):\n"
        f"  progress.py     → {emitter_dir}\n"
        f"  candidate_sheet → {sheet_dir}\n"
        "Both writers must resolve to the same directory or operators "
        "will tail the wrong run_progress.jsonl."
    )


def test_prophet_arb_state_dir_env_override_redirects_both_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "custom-state"
    monkeypatch.setenv("PROPHET_ARB_STATE_DIR", str(override))

    emitter_dir = ProgressEmitter().current_path.parent.resolve()
    sheet_dir = _candidate_sheet_default_dir().resolve()

    assert emitter_dir == override.resolve()
    assert sheet_dir == override.resolve()
