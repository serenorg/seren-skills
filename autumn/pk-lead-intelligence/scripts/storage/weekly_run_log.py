"""Append-only JSONL log of weekly status doc runs (Phase 5 — issue #779).

`/pk-status` reads this file to surface the latest weekly doc URL. The
log is tiny — one record per week — so an append-only flat file is the
right shape: no migrations, no schema, the existing `state/` directory
already collects skill-local persistence (see `state/playwright_storage.json`
and `state/sso_discovery.json`).

The append happens inside `agent.py --command weekly` after the live
Drive upload + share succeeds. Dry-run weekly invocations do not write,
so `/pk-status` never surfaces a fake URL from a rehearsal run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


_LOG_FILENAME = "weekly_status_runs.jsonl"


def _log_path(state_dir: Path) -> Path:
    return state_dir / _LOG_FILENAME


def append(state_dir: Path, record: dict) -> None:
    """Append one JSONL record to the log.

    Creates `state_dir` if missing so first-run cron does not need a
    pre-existing directory. Each record is one line; readers iterate
    line-by-line and the last non-empty line is "the latest run."
    """

    state_dir.mkdir(parents=True, exist_ok=True)
    path = _log_path(state_dir)
    line = json.dumps(record, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def latest(state_dir: Path) -> Optional[dict]:
    """Return the most recently appended record, or None.

    Iterates from end-of-file rather than loading the whole log into
    memory — the log is small in practice (≤52 entries/year) but the
    pattern matches what a larger ledger would need anyway.
    """

    path = _log_path(state_dir)
    if not path.exists():
        return None
    last: Optional[dict] = None
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                # Corrupt line — skip silently rather than crashing the
                # slash command. Operator can grep the file directly if
                # they suspect log damage.
                continue
            if isinstance(record, dict):
                last = record
    return last
