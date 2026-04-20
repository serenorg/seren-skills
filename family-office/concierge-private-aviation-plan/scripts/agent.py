#!/usr/bin/env python3
"""Agent runtime for the Private Aviation Coordination Plan skill (issue #429 augmentation).

Single-family-office tenancy. Self-contained: no cross-skill Python imports.

Flow:
    1. Load config (JSON; --config flag or default ./config.json).
    2. Run minimum-viable interview (TTY or fixture-driven).
    3. Render the Private Aviation Coordination Plan as markdown.
    4. Write artifact.md + interview.json + manifest.json to a skill-local
       timestamped directory.
    5. Optionally write memory entries to the knowledge skill's
       memory_objects table when config.memory_dsn is provided.
    6. Optionally push the artifact to external sinks:
       - SharePoint (microsoft-sharepoint publisher via Seren gateway)
       - Asana follow-up task (asana publisher via Seren gateway)
       - Snowflake FO_ARTIFACTS row (snowflake-connector-python, external-browser SSO by default)
       Each push is gated by presence of its config block; absent config = no-op.

Security posture (applies to every family-office skill):
    - Never log interview answers, artifact text, or credentials.
    - Credentials come from env vars only (SEREN_API_KEY, SNOWFLAKE_PASSWORD,
      SNOWFLAKE_PRIVATE_KEY_PATH). Never in config.json.
    - Parameterized SQL only.
    - SharePoint/Asana URLs redacted at INFO; DEBUG only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_NAME = "concierge-private-aviation-plan"
SKILL_DISPLAY = "Private Aviation Coordination Plan"
PILLAR = "complexity-management"
ARTIFACT_NAME = "Private Aviation Coordination Plan"

# Per-skill interview schema. Each entry is (key, prompt).
INTERVIEW_QUESTIONS: list[tuple[str, str]] = [


    ("operators_in_use", "Operators in use?"),
    ("preferred_aircraft", "Preferred aircraft class?"),
    ("typical_routes", "Typical routes?"),
    ("crew_preferences", "Crew preferences?"),
    ("catering_preferences", "Catering preferences?"),
]

DEFAULT_SEREN_API_BASE = "https://api.serendb.com"

logger = logging.getLogger(f"family_office.{SKILL_NAME}")


# ─── Redaction (confidentiality floor) ───────────────────────────────────

_PII_FIELD_PATTERN = re.compile(
    r"(?i)(ssn|ein|itin|account_number|routing_number|full_name_of_principal)"
)
_SSN_VALUE = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")
_EIN_VALUE = re.compile(r"\b\d{2}-?\d{7}\b")


def _redact_value(value: str) -> str:
    if not isinstance(value, str):
        return value
    v = _SSN_VALUE.sub("<redacted-ssn>", value)
    v = _EIN_VALUE.sub("<redacted-ein>", v)
    return v


def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of payload with PII-bearing fields redacted.

    Called before any external push so the structured payload that leaves
    the process never carries cleartext SSN / EIN / account numbers.
    """
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if _PII_FIELD_PATTERN.search(str(k)):
            out[k] = "<redacted>" if v else v
        elif isinstance(v, dict):
            out[k] = _redact_payload(v)
        elif isinstance(v, list):
            out[k] = [_redact_payload(x) if isinstance(x, dict) else _redact_value(x) for x in v]
        else:
            out[k] = _redact_value(v) if isinstance(v, str) else v
    return out


# ─── Helpers ─────────────────────────────────────────────────────────────

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _artifact_id(manifest_content_hash: str) -> str:
    return f"artifact:{SKILL_NAME}-{manifest_content_hash[:12]}"


# ─── Interview ────────────────────────────────────────────────────────────

def run_interview(
    *, fixture: dict[str, str] | None = None, tty: bool = True
) -> dict[str, str]:
    """Run the interview. If fixture is supplied, answers come from it
    (used by tests and by Claude-Code-driven invocation). Otherwise prompts
    the TTY. Missing fixture keys raise ValueError -- no silent defaults."""
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
                "XLSX companion renders and approval-gated execution "
                "actions are added by future PRs (see catalog tracking "
                "issues on seren-skills)."
            ),
            "",
            "## Confidentiality",
            "",
            (
                "Treat this artifact as `office-private` by default. Future "
                "PRs add confidentiality labels + DMS routing."
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


def write_artifact(answers: dict[str, str], *, base: Path) -> dict[str, Any]:
    out_dir = _canonical_out_dir(base)
    md = render_artifact(answers)
    (out_dir / "artifact.md").write_text(md, encoding="utf-8")
    (out_dir / "interview.json").write_text(
        json.dumps(answers, indent=2), encoding="utf-8"
    )
    content_hash = _hash_text(md)
    manifest = {
        "artifact_id": _artifact_id(content_hash),
        "skill": SKILL_NAME,
        "pillar": PILLAR,
        "artifact_name": ARTIFACT_NAME,
        "artifact_version": 1,
        "created_at": _iso_now(),
        "content_hash": content_hash,
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


# ─── Seren Gateway Client (inline; mirrors knowledge skill pattern) ──────

class GatewayClient:
    """Thin Seren API gateway client. Used to call MCP publishers over
    HTTP from within the skill. Authenticates with SEREN_API_KEY env var."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        import requests  # type: ignore[import-not-found]

        self.api_key = api_key or os.environ.get("SEREN_API_KEY")
        if not self.api_key:
            raise ValueError(
                "SEREN_API_KEY is required for external-sink pushes"
            )
        self.api_base = (
            api_base
            or os.environ.get("SEREN_API_BASE")
            or DEFAULT_SEREN_API_BASE
        ).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.api_key}"}
        )

    def call_publisher(
        self,
        publisher: str,
        method: str,
        path: str,
        *,
        body: Any | None = None,
    ) -> Any:
        url = f"{self.api_base}/publishers/{publisher}{path}"
        response = self.session.request(
            method=method, url=url, json=body, timeout=60
        )
        if response.status_code >= 400:
            # Redact credentials / URLs in the error surface.
            raise RuntimeError(
                f"publisher {publisher} {method} failed: "
                f"{response.status_code}"
            )
        try:
            return response.json()
        except ValueError:
            return {"status": response.status_code}


# ─── Push: SharePoint (microsoft-sharepoint publisher) ───────────────────

def push_to_sharepoint(
    manifest: dict[str, Any], *, config: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Upload artifact.md to SharePoint. No-op if config absent.

    config.sharepoint = {
        "site_id":   "<Graph site id>",
        "drive_id":  "<Graph drive id>",
        "folder_path": "/Seren/family-office"
    }
    """
    if not config:
        return None
    cfg = config.get("sharepoint") or {}
    if not cfg:
        return None
    required = ("site_id", "drive_id", "folder_path")
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(
            f"sharepoint config missing required keys: {missing}"
        )
    gw = GatewayClient()
    out_dir = Path(manifest["out_dir"])
    artifact_md = (out_dir / "artifact.md").read_text(encoding="utf-8")
    target_path = (
        f"{cfg['folder_path'].rstrip('/')}/"
        f"{SKILL_NAME}/{out_dir.name}/artifact.md"
    )
    payload_body = {
        "site_id": cfg["site_id"],
        "drive_id": cfg["drive_id"],
        "path": target_path,
        "content": artifact_md,
        "content_type": "text/markdown; charset=utf-8",
    }
    result = gw.call_publisher(
        "microsoft-sharepoint", "POST", "/files/upload", body=payload_body
    )
    logger.info(
        "sharepoint_push_ok skill=%s hash_prefix=%s",
        SKILL_NAME,
        manifest["content_hash"][:12],
    )
    # Do not log the returned URL at INFO — it leaks path structure.
    logger.debug("sharepoint_push_response skill=%s", SKILL_NAME)
    return {"publisher": "microsoft-sharepoint", "result": result}


# ─── Push: Asana (asana publisher) ───────────────────────────────────────

def push_to_asana(
    manifest: dict[str, Any],
    answers: dict[str, str],
    *,
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Create an Asana follow-up task for the artifact. No-op if config
    absent.

    config.asana = {
        "workspace_gid": "<gid>",
        "project_gid":   "<gid>",
        "assignee_gid":  "<gid>"   # optional
    }
    """
    if not config:
        return None
    cfg = config.get("asana") or {}
    if not cfg:
        return None
    required = ("workspace_gid", "project_gid")
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(f"asana config missing required keys: {missing}")
    gw = GatewayClient()
    task_body = {
        "data": {
            "name": f"Review {ARTIFACT_NAME} ({manifest['created_at'][:10]})",
            "notes": (
                f"Artifact ID: {manifest['artifact_id']}\n"
                f"Skill: {SKILL_NAME}\n"
                f"Pillar: {PILLAR}\n"
                f"Produced: {manifest['created_at']}\n"
                f"Local path: {manifest['out_dir']}\n"
                "See SharePoint for the rendered artifact."
            ),
            "workspace": cfg["workspace_gid"],
            "projects": [cfg["project_gid"]],
        }
    }
    if cfg.get("assignee_gid"):
        task_body["data"]["assignee"] = cfg["assignee_gid"]
    result = gw.call_publisher("asana", "POST", "/tasks", body=task_body)
    logger.info(
        "asana_task_created skill=%s hash_prefix=%s",
        SKILL_NAME,
        manifest["content_hash"][:12],
    )
    return {"publisher": "asana", "result": result}


# ─── Push: Snowflake (Python connector, external-browser SSO default) ────

def push_to_snowflake(
    manifest: dict[str, Any],
    answers: dict[str, str],
    *,
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Insert a row into FO_ARTIFACTS. No-op if config absent.

    config.snowflake = {
        "account":       "rendero.us-east-1",
        "user":          "seren_agent",
        "warehouse":     "FO_WH",
        "database":      "FO_DB",
        "schema":        "SEREN",
        "role":          "FO_WRITER",
        "authenticator": "externalbrowser"  # default; also: snowflake, oauth, snowflake_jwt
    }

    Customer prerequisite (one-time SQL, see snowflake_setup.sql):
      CREATE TABLE FO_ARTIFACTS (
        artifact_id STRING, pillar STRING, skill_name STRING,
        artifact_name STRING, artifact_version INTEGER,
        created_at TIMESTAMP_TZ, created_by STRING,
        content_hash STRING, structured_payload VARIANT
      );
    """
    if not config:
        return None
    cfg = config.get("snowflake") or {}
    if not cfg:
        return None
    required = ("account", "user", "warehouse", "database", "schema")
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(f"snowflake config missing required keys: {missing}")

    authenticator = cfg.get("authenticator") or "externalbrowser"
    conn_kwargs: dict[str, Any] = {
        "account": cfg["account"],
        "user": cfg["user"],
        "warehouse": cfg["warehouse"],
        "database": cfg["database"],
        "schema": cfg["schema"],
        "authenticator": authenticator,
    }
    if cfg.get("role"):
        conn_kwargs["role"] = cfg["role"]

    # Credentials strictly from env vars, never from config.json.
    # Validate auth-required env vars BEFORE importing the connector so a
    # caller without snowflake-connector-python installed still gets the
    # "missing env var" error (and so tests can exercise the validation
    # path without installing the heavy native dependency).
    if authenticator in {"snowflake", "oauth"}:
        password = os.environ.get("SNOWFLAKE_PASSWORD")
        if not password:
            raise ValueError(
                "SNOWFLAKE_PASSWORD env var required for "
                f"authenticator={authenticator!r}"
            )
        conn_kwargs["password"] = password
    elif authenticator == "snowflake_jwt":
        key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
        if not key_path:
            raise ValueError(
                "SNOWFLAKE_PRIVATE_KEY_PATH env var required for "
                "authenticator='snowflake_jwt'"
            )
        conn_kwargs["private_key_file"] = key_path
        if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
            conn_kwargs["private_key_file_pwd"] = os.environ[
                "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"
            ]
    # externalbrowser: no additional secret; the user's SSO handles auth.

    try:
        import snowflake.connector  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "snowflake-connector-python not installed; "
            "install to enable Snowflake push"
        ) from exc

    structured_payload = _redact_payload({"inputs": answers})
    structured_json = json.dumps(structured_payload)

    conn = snowflake.connector.connect(**conn_kwargs)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO FO_ARTIFACTS "
                "(artifact_id, pillar, skill_name, artifact_name, "
                " artifact_version, created_at, created_by, "
                " content_hash, structured_payload) "
                "SELECT %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, "
                "       PARSE_JSON(%s)",
                (
                    manifest["artifact_id"],
                    manifest["pillar"],
                    manifest["skill"],
                    manifest["artifact_name"],
                    manifest["artifact_version"],
                    SKILL_NAME,
                    manifest["content_hash"],
                    structured_json,
                ),
            )
            query_id = cur.sfqid
        finally:
            cur.close()
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "snowflake_insert_ok skill=%s query_id=%s",
        SKILL_NAME,
        query_id,
    )
    return {"publisher": "snowflake", "query_id": query_id}


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

    logger.info(
        "skill_run_completed skill=%s pillar=%s hash_prefix=%s",
        SKILL_NAME,
        PILLAR,
        manifest["content_hash"][:12],
    )

    # Optional memory write.
    memory_dsn = cfg.get("memory_dsn")
    if memory_dsn and not cfg.get("skip_memory", False):
        try:
            ids = write_memories(manifest, answers, dsn=memory_dsn)
            logger.info("memory_written skill=%s count=%d", SKILL_NAME, len(ids))
        except Exception as exc:  # noqa: BLE001 — controlled degradation
            logger.warning(
                "memory_write_failed skill=%s class=%s",
                SKILL_NAME,
                exc.__class__.__name__,
            )

    # Optional external sinks. Each push is independent; a failure in one
    # does not block the others. Push failures are logged, not fatal.
    for name, fn, args_pack in (
        ("sharepoint", push_to_sharepoint, (manifest,)),
        ("asana",      push_to_asana,      (manifest, answers)),
        ("snowflake",  push_to_snowflake,  (manifest, answers)),
    ):
        try:
            fn(*args_pack, config=cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "push_failed sink=%s skill=%s class=%s",
                name,
                SKILL_NAME,
                exc.__class__.__name__,
            )

    print(manifest["out_dir"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
