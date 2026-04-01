#!/usr/bin/env python3
"""BAT Sales Coach runtime with first-run DB bootstrap, returning-user behavior check, and OAuth verification."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- Force unbuffered stdout ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

DEFAULT_DRY_RUN = True
DEFAULT_COMMAND = "start_loop"
DEFAULT_PROJECT_NAME = "bat-sales-coach"
DEFAULT_DATABASE_NAME = "bat_sales_coach"
DEFAULT_SCHEMA_NAME = "bat_sales_coach"
DEFAULT_REGION = "aws-us-east-2"
SEREN_SKILLS_DOCS_URL = "https://docs.serendb.com/skills.md"
AVAILABLE_CONNECTORS = ["research", "storage"]
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "serendb_schema.sql"
SEREN_API_BASE = "https://console.serendb.com/api/v2"


class BatSkillError(Exception):
    """Base error for BAT sales coach."""


class SerenBootstrapError(BatSkillError):
    """Raised when Seren storage bootstrap fails."""


@dataclass
class SerenDbTarget:
    project_id: str
    branch_id: str
    database_name: str
    connection_string: str
    project_name: str
    branch_name: str
    created_project: bool = False
    created_database: bool = False


@dataclass
class BehaviorDueToday:
    task_id: str
    prospect_name: str
    organization: str
    title: str
    due_date: str
    status: str


@dataclass
class OAuthStatus:
    provider: str
    authenticated: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BAT Sales Coach agent.")
    parser.add_argument("--config", default="config.json", help="Path to config (default: config.json).")
    return parser.parse_args()


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Seren API client (minimal, stdlib-only)
# ---------------------------------------------------------------------------

class SerenApi:
    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        url = f"{SEREN_API_BASE}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._api_key}")
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def list_projects(self) -> list:
        return self._request("GET", "/projects").get("data", {}).get("projects", [])

    def create_project(self, *, name: str, region: str) -> dict:
        body = {"project": {"name": name, "region_id": region, "pg_version": 17}}
        return self._request("POST", "/projects", body).get("data", {}).get("project", {})

    def list_branches(self, project_id: str) -> list:
        return self._request("GET", f"/projects/{project_id}/branches").get("data", {}).get("branches", [])

    def list_databases(self, project_id: str, branch_id: str) -> list:
        return self._request("GET", f"/projects/{project_id}/branches/{branch_id}/databases").get("data", {}).get("databases", [])

    def create_database(self, *, project_id: str, branch_id: str, name: str) -> dict:
        body = {"database": {"name": name, "owner_name": "serendb_owner"}}
        return self._request("POST", f"/projects/{project_id}/branches/{branch_id}/databases", body).get("data", {}).get("database", {})

    def get_connection_string(self, *, project_id: str, branch_id: str) -> str:
        resp = self._request("GET", f"/projects/{project_id}/connection_uri?branch_id={branch_id}")
        return str(resp.get("data", {}).get("uri", ""))


def resolve_secret(config: dict, key: str) -> Optional[str]:
    val = os.getenv(key)
    if val:
        return val
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# DB bootstrap
# ---------------------------------------------------------------------------

def _patch_database(uri: str, db_name: str) -> str:
    parsed = urllib.parse.urlparse(uri)
    return urllib.parse.urlunparse(parsed._replace(path=f"/{db_name}"))


def resolve_or_create_serendb_target(
    api_key: str,
    *,
    project_name: str,
    database_name: str,
    region: str,
) -> SerenDbTarget:
    api = SerenApi(api_key=api_key)

    projects = api.list_projects()
    project = next((p for p in projects if str(p.get("name", "")).lower() == project_name.lower()), None)
    created_project = False
    if not project:
        project = api.create_project(name=project_name, region=region)
        created_project = True

    project_id = str(project.get("id") or "")
    if not project_id:
        raise SerenBootstrapError("Unable to determine BAT storage project_id")

    branches = api.list_branches(project_id)
    if not branches:
        raise SerenBootstrapError(f"No branches available for BAT storage project {project_id}")

    default_branch_id = project.get("default_branch_id") if isinstance(project, dict) else None
    branch = None
    if default_branch_id:
        branch = next((b for b in branches if str(b.get("id")) == str(default_branch_id)), None)
    if not branch:
        branch = next((b for b in branches if str(b.get("name", "")).lower() in {"main", "production"}), None)
    if not branch:
        branch = branches[0]

    branch_id = str(branch.get("id") or "")
    branch_name = str(branch.get("name") or "main")
    if not branch_id:
        raise SerenBootstrapError("Unable to determine BAT storage branch_id")

    databases = api.list_databases(project_id, branch_id)
    db_names = {str(d.get("name")) for d in databases if d.get("name")}
    created_database = False
    if database_name not in db_names:
        api.create_database(project_id=project_id, branch_id=branch_id, name=database_name)
        created_database = True

    conn = _patch_database(api.get_connection_string(project_id=project_id, branch_id=branch_id), database_name)
    return SerenDbTarget(
        project_id=project_id,
        branch_id=branch_id,
        database_name=database_name,
        connection_string=conn,
        project_name=str(project.get("name") or project_name),
        branch_name=branch_name,
        created_project=created_project,
        created_database=created_database,
    )


def storage_bootstrap_sql(schema_name: str) -> List[str]:
    if not SCHEMA_PATH.exists():
        raise SerenBootstrapError(f"Schema file not found: {SCHEMA_PATH}")
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    rendered = raw.replace("{{schema_name}}", schema_name)
    statements = [part.strip() for part in rendered.split(";") if part.strip()]
    if not statements:
        raise SerenBootstrapError(f"Schema file is empty: {SCHEMA_PATH}")
    return statements


def psycopg_connect(dsn: str):
    import psycopg
    return psycopg.connect(dsn)


def apply_storage_bootstrap(connection_string: str, schema_name: str) -> int:
    statements = storage_bootstrap_sql(schema_name)
    try:
        with psycopg_connect(connection_string) as conn:
            with conn.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
            conn.commit()
    except Exception as exc:
        raise SerenBootstrapError(f"Failed to apply BAT schema: {exc}") from exc
    return len(statements)


def ensure_storage(config: dict) -> dict:
    storage_cfg = config.get("storage") if isinstance(config.get("storage"), dict) else {}

    project_name = str(storage_cfg.get("project_name") or DEFAULT_PROJECT_NAME)
    database_name = str(storage_cfg.get("database_name") or DEFAULT_DATABASE_NAME)
    schema_name = str(storage_cfg.get("schema_name") or DEFAULT_SCHEMA_NAME)
    region = str(storage_cfg.get("region") or DEFAULT_REGION)
    connection_string = storage_cfg.get("connection_string") or os.getenv("SERENDB_URL")
    api_key = resolve_secret(config, "SEREN_API_KEY")

    target: Optional[SerenDbTarget] = None
    if not connection_string:
        if not api_key:
            raise SerenBootstrapError(
                f"SEREN_API_KEY is required to auto-provision BAT storage. "
                f"Create an account at {SEREN_SKILLS_DOCS_URL}."
            )
        target = resolve_or_create_serendb_target(
            api_key,
            project_name=project_name,
            database_name=database_name,
            region=region,
        )
        connection_string = target.connection_string

    executed = apply_storage_bootstrap(connection_string, schema_name)
    result: Dict[str, Any] = {
        "status": "ok",
        "schema_name": schema_name,
        "database_name": database_name,
        "project_name": project_name,
        "statements_executed": executed,
        "connection_string": connection_string,
    }
    if target:
        result.update({
            "project_id": target.project_id,
            "branch_id": target.branch_id,
            "branch_name": target.branch_name,
            "created_project": target.created_project,
            "created_database": target.created_database,
        })
    return result


# ---------------------------------------------------------------------------
# Returning-user behavior check
# ---------------------------------------------------------------------------

def query_behaviors_due_today(connection_string: str, schema_name: str) -> List[BehaviorDueToday]:
    sql = f"""
        SELECT id, prospect_name, organization, title, due_date, status
        FROM {schema_name}.behavior_tasks
        WHERE status = 'planned' AND due_date IS NOT NULL AND due_date <= CURRENT_DATE::text
        ORDER BY due_date, created_at
    """
    rows: List[BehaviorDueToday] = []
    try:
        with psycopg_connect(connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                for row in cur.fetchall():
                    rows.append(BehaviorDueToday(
                        task_id=str(row[0]),
                        prospect_name=str(row[1] or ""),
                        organization=str(row[2] or ""),
                        title=str(row[3] or ""),
                        due_date=str(row[4] or ""),
                        status=str(row[5] or "planned"),
                    ))
    except Exception:
        pass  # Table may not exist yet on first run
    return rows


def format_behavior_table(behaviors: List[BehaviorDueToday]) -> str:
    if not behaviors:
        return "No behaviors due today."
    header = "| Prospect | Organization | Behavior | Due Date | Status |"
    sep = "|----------|--------------|----------|----------|--------|"
    rows = [
        f"| {b.prospect_name} | {b.organization} | {b.title} | {b.due_date} | {b.status} |"
        for b in behaviors
    ]
    return "\n".join([header, sep] + rows)


# ---------------------------------------------------------------------------
# OAuth authentication check
# ---------------------------------------------------------------------------

def check_oauth_providers(config: dict) -> List[OAuthStatus]:
    results: List[OAuthStatus] = []
    for provider in ("microsoft", "google"):
        env_key = f"{provider.upper()}_OAUTH_TOKEN"
        token = os.getenv(env_key)
        results.append(OAuthStatus(provider=provider, authenticated=bool(token)))
    return results


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_once(config: dict, dry_run: bool) -> dict:
    result: Dict[str, Any] = {
        "status": "ok",
        "dry_run": dry_run,
        "connectors": AVAILABLE_CONNECTORS,
        "skill": "bat-sales-coach",
    }

    # Step 1: Ensure storage (first-run bootstrap)
    try:
        storage_result = ensure_storage(config)
        result["storage"] = storage_result
        connection_string = storage_result.get("connection_string")
        schema_name = storage_result.get("schema_name", DEFAULT_SCHEMA_NAME)
    except SerenBootstrapError as exc:
        result["status"] = "error"
        result["error_code"] = "storage_bootstrap_failed"
        result["error_message"] = str(exc)
        return result

    # Step 2: Check for returning-user behaviors due today
    behaviors_due: List[BehaviorDueToday] = []
    if connection_string:
        behaviors_due = query_behaviors_due_today(connection_string, schema_name)
    result["behaviors_due_today"] = [
        {"task_id": b.task_id, "prospect_name": b.prospect_name, "organization": b.organization,
         "title": b.title, "due_date": b.due_date, "status": b.status}
        for b in behaviors_due
    ]
    result["behaviors_due_count"] = len(behaviors_due)
    result["behavior_table"] = format_behavior_table(behaviors_due)

    # Step 3: Check OAuth authentication
    oauth_statuses = check_oauth_providers(config)
    result["oauth"] = [
        {"provider": o.provider, "authenticated": o.authenticated}
        for o in oauth_statuses
    ]

    return result


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    result = run_once(config=config, dry_run=dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
