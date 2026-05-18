"""Canonical state-dir resolver for prophet-arb-bot (issue #693).

Every module that reads from or writes to the skill's state directory
must go through `resolve_state_dir()`. The previous split between
`progress.py` (which used `~/.config/seren/...`) and
`discovery/candidate_sheet.py` (which used `<repo>/state/` via
`__file__`) made operators tail the wrong `run_progress.jsonl` and miss
all stage events during dev / verification runs.

Resolution order:
    1. `PROPHET_ARB_STATE_DIR` env var (operator override).
    2. `~/.config/seren/skills/prophet-arb-bot/state/` (canonical
       Seren Desktop runtime dir, matches SKILL.md "Skill runtime
       directory" header).
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_state_dir() -> Path:
    override = os.environ.get("PROPHET_ARB_STATE_DIR") or ""
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "seren" / "skills" / "prophet-arb-bot" / "state"
