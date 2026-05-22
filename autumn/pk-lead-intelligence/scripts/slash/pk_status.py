"""`/pk-status` slash command (Phase 5 — issue #779).

Surfaces the most recent weekly status doc to the operator. If the
latest log entry is from the current ISO week, prints the URL block
that the operator can click through to. Otherwise prints the on-demand
offer so the operator knows the cron is behind and can manually fire
`--command weekly --allow-live`.

The current ISO week is computed in America/New_York to match the
weekly cron's timezone — the doc's week_label is what the renderer
stamps and it is NY-local, so comparing in UTC would produce a
boundary-day off-by-one every Sunday evening.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `from scripts.* import …` work when this file is launched as a
# script. Mirrors agent.py's pattern.
_SKILL_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

from scripts.storage import weekly_run_log  # noqa: E402


# Resolve the same default state-dir the cron runner uses.
_DEFAULT_STATE_DIR = Path(_SKILL_ROOT) / "state"


def _now_utc() -> datetime:
    """Seam — tests inject a fixed timestamp."""
    return datetime.now(tz=timezone.utc)


def _current_iso_week_label() -> str:
    """Render the current week as `YYYY-Www` in America/New_York.

    America/New_York matches the weekly cron's configured timezone
    (`schedule.weekly_cron = "0 7 * * 2"` in `America/New_York`). The
    weekly doc's `week_label` is built from `now()` in the agent
    process timezone; both ends agree if we localize the same way.
    """

    try:
        # Python 3.9+ stdlib. The skill targets 3.11+ per SKILL.md.
        from zoneinfo import ZoneInfo

        local = _now_utc().astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # Defensive fallback — if zoneinfo data is missing the
        # comparison may be off by a day at the week boundary; still
        # better than crashing the slash command.
        local = _now_utc()
    iso = local.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pk-status",
        description=(
            "Surface the most recent PK weekly status doc URL, or offer "
            "to trigger an on-demand `--command weekly` run when the "
            "current week has not been published yet."
        ),
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=_DEFAULT_STATE_DIR,
        help=(
            "Directory containing weekly_status_runs.jsonl. Defaults to "
            "the skill's state/ directory."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    record = weekly_run_log.latest(args.state_dir)
    current_week = _current_iso_week_label()

    if record is None or record.get("week_label") != current_week:
        print("No weekly doc for the current week yet.")
        print("Run: python scripts/agent.py --command weekly --allow-live")
        return 0

    title = record.get("title") or f"PK Weekly Status — {record.get('week_label', '?')}"
    print(f"Latest: {title}")
    if record.get("doc_url"):
        print(f"  URL: {record['doc_url']}")
    if record.get("shared_with"):
        print(f"  Shared with: {record['shared_with']}")
    if record.get("generated_at_utc"):
        print(f"  Generated: {record['generated_at_utc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
