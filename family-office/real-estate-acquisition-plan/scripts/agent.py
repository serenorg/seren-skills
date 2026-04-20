#!/usr/bin/env python3
"""Agent runtime for the Real Estate Acquisition Plan skill.

Single-family-office tenancy. Self-contained: no cross-skill Python imports.

Flow:
    1. Load config (JSON; --config flag or default ./config.json).
    2. Run minimum-viable interview (TTY or fixture-driven).
    3. Render the Real Estate Acquisition Plan as markdown.
    4. Write artifact.md + interview.json + manifest.json to a skill-local
       timestamped directory.
    5. Optionally write memory entries to the knowledge skill's
       memory_objects table when config.memory_dsn is provided.

Security posture (applies to every family-office skill):
    - Never log interview answers or artifact text.
    - Never log memory_dsn.
    - Parameterize every SQL call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_NAME = "real-estate-acquisition-plan"
SKILL_DISPLAY = "Real Estate Acquisition Plan"
PILLAR = "complexity-management"
ARTIFACT_NAME = "Real Estate Acquisition Plan"

# Per-skill interview schema. Each entry is (key, prompt).
INTERVIEW_QUESTIONS: list[tuple[str, str]] = [
    ("property_address", "Property address (or identifier)?"),
    ("use_case", "Use case (primary residence / investment / family compound)?"),
    ("purchase_price", "Purchase price?"),
    ("financing_plan", "Financing plan (cash / mortgage / seller financing)?"),
    ("owning_entity", "Owning entity (individual / LLC / trust)?"),
    ("target_close_date", "Target close date?"),
]

logger = logging.getLogger(f"family_office.{SKILL_NAME}")


# ─── Helpers (inline — no _base/ import) ─────────────────────────────────

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _redact(value: str, keep: int = 0) -> str:
    if not value:
        return ""
    if keep >= len(value):
        return value
    return value[:keep] + ("*" * max(1, len(value) - keep))


# ─── Interview ────────────────────────────────────────────────────────────

def run_interview(
    *, fixture: dict[str, str] | None = None, tty: bool = True
) -> dict[str, str]:
    """Run the interview. If fixture is supplied, answers come from it
    (used by tests and by Claude-Code-driven invocation). Otherwise prompts
    the TTY. Missing fixture keys raise ValueError -- no silent defaults,
    because a defaulted interview would produce a wrong artifact."""
    answers: dict[str, str] = {}
    for key, prompt in INTERVIEW_QUESTIONS:
        if fixture is not None:
            if key not in fixture:
                raise ValueError(
                    f"interview fixture missing required key: {key!r}"
                )
            answers[key] = str(fixture[key]).strip()
        else:
            if not tty:
                raise RuntimeError(
                    "interview requires either a fixture or a TTY"
                )
            answers[key] = input(f"{prompt}  ").strip()
    return answers


# ─── Artifact rendering ──────────────────────────────────────────────────

def render_artifact(answers: dict[str, str]) -> str:
    lines = [
        f"# {ARTIFACT_NAME}",
        "",
        f"- **Pillar:** {PILLAR.replace('-', ' ').title()}",
        f"- **Produced:** {_iso_now()}",
        f"- **Skill:** `{SKILL_NAME}`",
        "",
        "## Inputs captured",
        "",
    ]
    for key, prompt in INTERVIEW_QUESTIONS:
        label = prompt.rstrip("?").rstrip()
        value = answers.get(key, "")
        lines.append(f"- **{label}:** {value}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            (
                "This is a first-iteration deliverable. PDF, DOCX, and "
                "XLSX companion renders, DMS push, Snowflake ingest, and "
                "approval-gated execution actions are added by the "
                "execution-pipeline PR tracked under issue #427."
            ),
            "",
            "## Confidentiality",
            "",
            (
                "Treat this artifact as `office-private` by default. When "
                "the execution pipeline ships, the skill will read the "
                "configured confidentiality label from the office record "
                "and route destinations accordingly."
            ),
            "",
        ]
    )
    return "\n".join(lines)


# ─── Write ───────────────────────────────────────────────────────────────

def _canonical_out_dir(base: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = base / "artifacts" / "family-office" / SKILL_NAME / stamp
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_artifact(
    answers: dict[str, str], *, base: Path
) -> dict[str, Any]:
    out_dir = _canonical_out_dir(base)
    md = render_artifact(answers)
    (out_dir / "artifact.md").write_text(md, encoding="utf-8")
    (out_dir / "interview.json").write_text(
        json.dumps(answers, indent=2), encoding="utf-8"
    )
    manifest = {
        "skill": SKILL_NAME,
        "pillar": PILLAR,
        "artifact_name": ARTIFACT_NAME,
        "artifact_version": 1,
        "created_at": _iso_now(),
        "content_hash": _hash_text(md),
        "out_dir": str(out_dir),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


# ─── Memory write (optional, psycopg) ────────────────────────────────────

def _memory_id(memory_type: str) -> str:
    return f"memory:{memory_type}-{uuid.uuid4().hex[:8]}"


def write_memories(
    manifest: dict[str, Any],
    answers: dict[str, str],
    *,
    dsn: str,
) -> list[str]:
    """Write one decision + one assumption + one open_question + one
    commitment memory per run. Uses psycopg; parameterized SQL only.

    Returns list of memory ids written. Raises on connection failure --
    callers decide whether to proceed without memory.
    """
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("psycopg is required for memory writes") from exc

    ids: list[str] = []
    memories = [
        (
            "decision",
            f"Produced {ARTIFACT_NAME} on {manifest['created_at']}.",
        ),
        (
            "assumption",
            f"{ARTIFACT_NAME} generated from advisor-supplied inputs "
            f"(interview.json, hash {manifest['content_hash'][:12]}).",
        ),
        (
            "open_question",
            f"Confirm {ARTIFACT_NAME} with principal before distributing.",
        ),
        (
            "commitment",
            f"Advisor to review {ARTIFACT_NAME} and address open items.",
        ),
    ]
    with psycopg.connect(dsn, autocommit=False) as conn:
        for mtype, claim in memories:
            mid = _memory_id(mtype)
            conn.execute(
                "INSERT INTO memory_objects "
                "(id, memory_type, key_claim, subject, "
                " confidence_score, importance_score, validity_status, "
                " source, source_id, entity_refs, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, "
                "         now(), now())",
                (
                    mid,
                    mtype,
                    claim,
                    ARTIFACT_NAME,
                    "medium",
                    "medium",
                    SKILL_NAME,
                    manifest["out_dir"],
                    [f"skill:{SKILL_NAME}", f"pillar:{PILLAR}"],
                ),
            )
            ids.append(mid)
        conn.commit()
    return ids


# ─── CLI entry ───────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=SKILL_DISPLAY)
    p.add_argument("--config", default="config.json", help="Config JSON path")
    p.add_argument("--cwd", default=".", help="Base directory for artifacts")
    p.add_argument(
        "--no-tty",
        action="store_true",
        help="Fail rather than prompt for missing fixture keys",
    )
    return p.parse_args(argv)


def load_config(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args(argv)
    cfg = load_config(args.config)

    fixture = cfg.get("interview_answers")
    tty = not args.no_tty

    answers = run_interview(fixture=fixture, tty=tty)
    manifest = write_artifact(answers, base=Path(args.cwd))

    # Never log answers, manifest content, or the memory DSN.
    logger.info(
        "skill_run_completed skill=%s pillar=%s hash_prefix=%s",
        SKILL_NAME,
        PILLAR,
        manifest["content_hash"][:12],
    )

    memory_dsn = cfg.get("memory_dsn")
    if memory_dsn and not cfg.get("skip_memory", False):
        try:
            ids = write_memories(manifest, answers, dsn=memory_dsn)
            logger.info(
                "memory_written skill=%s count=%d", SKILL_NAME, len(ids)
            )
        except Exception as exc:  # noqa: BLE001 — controlled degradation
            logger.warning(
                "memory_write_failed skill=%s class=%s",
                SKILL_NAME,
                exc.__class__.__name__,
            )

    print(manifest["out_dir"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
