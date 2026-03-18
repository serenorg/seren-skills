#!/usr/bin/env python3
"""Runtime for prophet-adversarial-auditor with explicit Prophet auth and storage bootstrap."""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DRY_RUN = True
DEFAULT_COMMAND = "run"
DEFAULT_PROJECT_NAME = "prophet"
DEFAULT_DATABASE_NAME = "prophet"
DEFAULT_SCHEMA_NAME = "prophet_adversarial_auditor"
DEFAULT_REGION = "aws-us-east-2"
DEFAULT_PROPHET_BASE_URL = "https://app.prophetmarket.ai"
SEREN_SKILLS_DOCS_URL = "https://docs.serendb.com/skills.md"
AVAILABLE_CONNECTORS = ["storage"]
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "serendb_schema.sql"
VIEWER_WALLET_BALANCE_QUERY = """
query ViewerWalletBalance {
  viewer {
    walletBalance {
      availableCents
      totalCents
      safeAddress
      safeDeployed
      __typename
    }
    __typename
  }
}
""".strip()


class ProphetSkillError(RuntimeError):
    """Base error for runtime failures."""


class ProphetAuthError(ProphetSkillError):
    """Raised when Prophet auth cannot be validated."""


class SerenBootstrapError(ProphetSkillError):
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run prophet-adversarial-auditor.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    return parser.parse_args()


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_command(value: Any) -> str:
    command = str(value or DEFAULT_COMMAND).strip().lower()
    if command not in {"setup", "run", "status"}:
        raise ProphetSkillError(f"Unsupported command: {command}")
    return command


def _config_inputs(config: dict) -> dict:
    return config.get("inputs", {}) if isinstance(config.get("inputs"), dict) else {}


def resolve_secret(config: dict, name: str) -> Optional[str]:
    secret_block = config.get("secrets")
    if isinstance(secret_block, dict):
        value = secret_block.get(name)
        if value:
            return str(value)
    value = os.getenv(name)
    if value:
        return value
    return None


def _error_result(message: str, *, error_code: str, dry_run: bool, command: str, details: Optional[dict] = None) -> dict:
    payload = {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "dry_run": dry_run,
        "command": command,
    }
    if details:
        payload["details"] = details
    return payload


class ProphetApi:
    def __init__(self, session_token: str, base_url: Optional[str] = None):
        if not session_token:
            raise ValueError("PROPHET_SESSION_TOKEN is required")
        self.session_token = session_token
        self.base_url = (base_url or os.getenv("PROPHET_BASE_URL") or DEFAULT_PROPHET_BASE_URL).rstrip("/")

    def _request(self, query: str, operation_name: str) -> Dict[str, Any]:
        url = f"{self.base_url}/api/graphql"
        body = {"query": query, "operationName": operation_name}
        req = urllib.request.Request(
            url=url,
            method="POST",
            data=json.dumps(body).encode("utf-8"),
        )
        req.add_header("Authorization", f"Bearer {self.session_token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
        except Exception as exc:
            raise ProphetAuthError(f"Prophet auth probe failed: {exc}") from exc

        try:
            return json.loads(payload) if payload else {}
        except json.JSONDecodeError as exc:
            raise ProphetAuthError("Prophet auth probe returned invalid JSON") from exc

    def viewer_wallet_balance(self) -> dict:
        payload = self._request(VIEWER_WALLET_BALANCE_QUERY, "ViewerWalletBalance")
        viewer = payload.get("data", {}).get("viewer")
        if not viewer:
            raise ProphetAuthError(
                "Prophet session token was accepted by the endpoint but did not resolve an authenticated viewer"
            )
        return viewer


class SerenApi:
    def __init__(self, api_key: str, api_base: Optional[str] = None):
        if not api_key:
            raise ValueError("SEREN_API_KEY is required")
        self.api_key = api_key
        self.api_base = (api_base or os.getenv("SEREN_API_BASE") or "https://api.serendb.com/publishers/seren-db").rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})

        req = urllib.request.Request(url=url, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")
        raw = json.dumps(body).encode("utf-8") if body is not None else None

        try:
            with urllib.request.urlopen(req, data=raw, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
        except Exception as exc:
            raise SerenBootstrapError(f"Seren API request failed ({method} {path}): {exc}") from exc

        try:
            return json.loads(payload) if payload else {}
        except json.JSONDecodeError as exc:
            raise SerenBootstrapError(f"Seren API returned invalid JSON for {method} {path}") from exc

    @staticmethod
    def _as_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "projects", "branches", "databases"):
                items = data.get(key)
                if isinstance(items, list):
                    return items
        return []

    def list_projects(self) -> List[Dict[str, Any]]:
        return self._as_list(self._request("GET", "/projects"))

    def create_project(self, name: str, region: str) -> Dict[str, Any]:
        payload = self._request("POST", "/projects", body={"name": name, "region": region})
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def list_branches(self, project_id: str) -> List[Dict[str, Any]]:
        return self._as_list(self._request("GET", f"/projects/{project_id}/branches"))

    def list_databases(self, project_id: str, branch_id: str) -> List[Dict[str, Any]]:
        return self._as_list(self._request("GET", f"/projects/{project_id}/branches/{branch_id}/databases"))

    def create_database(self, project_id: str, branch_id: str, name: str) -> Dict[str, Any]:
        payload = self._request(
            "POST",
            f"/projects/{project_id}/branches/{branch_id}/databases",
            body={"name": name},
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def get_connection_string(self, project_id: str, branch_id: str, role: str = "serendb_owner") -> str:
        payload = self._request(
            "GET",
            f"/projects/{project_id}/branches/{branch_id}/connection-string",
            query={"role": role, "pooled": "false"},
        )
        data = payload.get("data")
        if isinstance(data, dict) and data.get("connection_string"):
            return str(data["connection_string"])
        if payload.get("connection_string"):
            return str(payload["connection_string"])
        raise SerenBootstrapError("Could not resolve connection string from Seren API")


def _patch_database(connection_string: str, database_name: str) -> str:
    parsed = urllib.parse.urlparse(connection_string)
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, f"/{database_name}", parsed.params, parsed.query, parsed.fragment)
    )


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
        raise SerenBootstrapError("Unable to determine Prophet storage project_id")

    branches = api.list_branches(project_id)
    if not branches:
        raise SerenBootstrapError(f"No branches available for Prophet storage project {project_id}")

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
        raise SerenBootstrapError("Unable to determine Prophet storage branch_id")

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


def psycopg_connect(dsn: str):  # pragma: no cover - exercised via tests with monkeypatch
    import psycopg

    return psycopg.connect(dsn)


def apply_storage_bootstrap(connection_string: str, schema_name: str) -> int:
    statements = storage_bootstrap_sql(schema_name)
    try:
        with psycopg_connect(connection_string) as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)
            conn.commit()
    except Exception as exc:
        raise SerenBootstrapError(f"Failed to apply Prophet storage bootstrap: {exc}") from exc
    return len(statements)


def ensure_storage(config: dict) -> dict:
    storage_cfg = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    if not _bool(storage_cfg.get("auto_bootstrap"), True):
        return {
            "status": "skipped",
            "reason": "auto_bootstrap_disabled",
            "schema_name": str(storage_cfg.get("schema_name") or DEFAULT_SCHEMA_NAME),
        }

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
                f"SEREN_API_KEY is required to auto-provision Prophet storage. Create an account at {SEREN_SKILLS_DOCS_URL}."
            )
        target = resolve_or_create_serendb_target(
            api_key,
            project_name=project_name,
            database_name=database_name,
            region=region,
        )
        connection_string = target.connection_string

    executed = apply_storage_bootstrap(connection_string, schema_name)
    result = {
        "status": "ok",
        "schema_name": schema_name,
        "database_name": database_name,
        "project_name": project_name,
        "statements_executed": executed,
        "auto_provisioned": True,
    }
    if target:
        result.update(
            {
                "project_id": target.project_id,
                "branch_id": target.branch_id,
                "branch_name": target.branch_name,
                "created_project": bool(getattr(target, "created_project", False)),
                "created_database": bool(getattr(target, "created_database", False)),
            }
        )
    return result


def validate_prophet_access(config: dict) -> dict:
    token = resolve_secret(config, "PROPHET_SESSION_TOKEN")
    if not token:
        raise ProphetAuthError(
            "Missing PROPHET_SESSION_TOKEN. Use the Privy JWT from localStorage['privy:token'] and send it as Authorization: Bearer <token>."
        )
    viewer = ProphetApi(token).viewer_wallet_balance()
    wallet_balance = viewer.get("walletBalance") or {}
    return {
        "status": "ok",
        "required_header": "Authorization: Bearer <PROPHET_SESSION_TOKEN>",
        "token_source": "localStorage['privy:token'] from an authenticated Prophet browser session",
        "viewer": {
            "wallet_balance_total_cents": wallet_balance.get("totalCents"),
            "wallet_balance_available_cents": wallet_balance.get("availableCents"),
            "safe_address": wallet_balance.get("safeAddress"),
            "safe_deployed": wallet_balance.get("safeDeployed"),
        },
    }


def run_once(config: dict, dry_run: bool) -> dict:
    inputs = _config_inputs(config)
    command = _normalize_command(inputs.get("command"))
    strict_mode = _bool(inputs.get("strict_mode"), True)
    lookback_days = int(inputs.get("lookback_days") or 30)
    severity_threshold = str(inputs.get("severity_threshold") or "medium").strip().lower()
    include_loss_hypotheses = _bool(inputs.get("include_loss_hypotheses"), True)

    if lookback_days < 1 or lookback_days > 90:
        return _error_result(
            "Invalid lookback_days value",
            error_code="invalid_lookback_days",
            dry_run=dry_run,
            command=command,
            details={"lookback_days": lookback_days},
        )
    if severity_threshold not in {"low", "medium", "high"}:
        return _error_result(
            "Invalid severity_threshold value",
            error_code="invalid_severity_threshold",
            dry_run=dry_run,
            command=command,
            details={"severity_threshold": severity_threshold},
        )

    try:
        storage = ensure_storage(config) if command in {"setup", "run"} else {"status": "not_run"}
        auth = validate_prophet_access(config)
    except ProphetSkillError as exc:
        if strict_mode or command != "setup":
            error_code = "missing_seren_api_key" if SEREN_SKILLS_DOCS_URL in str(exc) else "auth_or_bootstrap_failed"
            details = {"docs_url": SEREN_SKILLS_DOCS_URL} if error_code == "missing_seren_api_key" else None
            return _error_result(str(exc), error_code=error_code, dry_run=dry_run, command=command, details=details)
        storage = {"status": "skipped", "reason": "setup_non_strict"}
        auth = {
            "status": "warning",
            "message": str(exc),
            "required_header": "Authorization: Bearer <PROPHET_SESSION_TOKEN>",
            "token_source": "localStorage['privy:token'] from an authenticated Prophet browser session",
        }

    result = {
        "status": "ok",
        "skill": "prophet-adversarial-auditor",
        "command": command,
        "dry_run": dry_run,
        "connectors": AVAILABLE_CONNECTORS,
        "input_keys": sorted(inputs.keys()),
        "auth": auth,
        "storage": storage,
        "analysis_window_days": lookback_days,
        "severity_threshold": severity_threshold,
        "include_loss_hypotheses": include_loss_hypotheses,
    }
    if command == "run":
        result["analysis_mode"] = "dry-run" if dry_run else "live"
    return result


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = _bool(config.get("dry_run"), DEFAULT_DRY_RUN)
    result = run_once(config=config, dry_run=dry_run)
    print(json.dumps(result))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
