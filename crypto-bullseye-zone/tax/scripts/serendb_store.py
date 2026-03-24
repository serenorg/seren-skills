#!/usr/bin/env python3
"""Persist reconciliation artifacts into a user's hosted SerenDB instance."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


def _api_base() -> str:
    return os.getenv("SEREN_API_BASE", "https://api.serendb.com")


class SerenApiError(RuntimeError):
    """Raised when Seren API calls fail."""


@dataclass
class SerenTarget:
    project_id: str
    branch_id: str
    database_name: str
    connection_string: str


class SerenApi:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("SEREN_API_KEY is required")
        self.api_key = api_key

    def _request(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{_api_base()}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})

        req = urllib.request.Request(url=url, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except Exception as exc:
            raise SerenApiError(f"API request failed for {path}: {exc}") from exc

    def list_projects(self) -> List[Dict[str, Any]]:
        payload = self._request("GET", "/publishers/seren-db/projects")
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(payload, list):
            return payload
        return []

    def list_branches(self, project_id: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"/publishers/seren-db/projects/{project_id}/branches")
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            branches = data.get("branches")
            if isinstance(branches, list):
                return branches
        return []

    def list_databases(self, project_id: str, branch_id: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"/publishers/seren-db/projects/{project_id}/branches/{branch_id}/databases")
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            databases = data.get("databases") or data.get("items")
            if isinstance(databases, list):
                return databases
        return []

    def get_connection_string(self, project_id: str, branch_id: str, role: str = "serendb_owner") -> str:
        payload = self._request(
            "GET",
            f"/publishers/seren-db/projects/{project_id}/branches/{branch_id}/connection-string",
            query={"role": role, "pooled": "false"},
        )
        data = payload.get("data")
        if isinstance(data, dict):
            conn = data.get("connection_string")
            if conn:
                return conn
        conn = payload.get("connection_string")
        if conn:
            return conn
        raise SerenApiError("Unable to resolve connection string from Seren API response")

    def resolve_target(self) -> SerenTarget:
        project_id = os.getenv("SEREN_PROJECT_ID")
        branch_id = os.getenv("SEREN_BRANCH_ID")
        database_name = os.getenv("SEREN_DATABASE_NAME")

        projects = self.list_projects()
        if not projects:
            raise SerenApiError("No projects available for this API key")

        if not project_id:
            project_id = projects[0].get("id")
        if not project_id:
            raise SerenApiError("Unable to determine project_id")

        branches = self.list_branches(project_id)
        if not branches:
            raise SerenApiError(f"No branches found for project {project_id}")

        if not branch_id:
            default_branch_id = None
            for project in projects:
                if project.get("id") == project_id and project.get("default_branch_id"):
                    default_branch_id = project.get("default_branch_id")
                    break
            branch_id = default_branch_id or branches[0].get("id")
        if not branch_id:
            raise SerenApiError("Unable to determine branch_id")

        databases = self.list_databases(project_id, branch_id)
        if not databases:
            raise SerenApiError(f"No databases found for project={project_id}, branch={branch_id}")

        if not database_name:
            names = [item.get("name") for item in databases if item.get("name")]
            database_name = "serendb" if "serendb" in names else names[0]
        if not database_name:
            raise SerenApiError("Unable to determine database_name")

        connection_string = self.get_connection_string(project_id, branch_id)

        return SerenTarget(
            project_id=project_id,
            branch_id=branch_id,
            database_name=database_name,
            connection_string=connection_string,
        )


def _patch_dbname(connection_string: str, database_name: str) -> str:
    parsed = urllib.parse.urlparse(connection_string)
    path = "/" + database_name
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


def _require_psycopg():
    try:
        import psycopg  # type: ignore

        return psycopg
    except Exception as exc:
        raise RuntimeError("Install dependency: pip install 'psycopg[binary]>=3.2.0'") from exc


def ensure_schema(conn: Any) -> None:
    ddl = """
    create schema if not exists crypto_tax;

    create table if not exists crypto_tax.reconciliation_runs (
      run_id text primary key,
      created_at timestamptz not null default now(),
      input_1099da_path text,
      input_tax_path text,
      summary jsonb not null
    );

    create table if not exists crypto_tax.normalized_1099da (
      run_id text not null,
      record_id text not null,
      payload jsonb not null,
      primary key (run_id, record_id)
    );

    create table if not exists crypto_tax.resolved_lots (
      run_id text not null,
      record_id text not null,
      payload jsonb not null,
      primary key (run_id, record_id)
    );

    create table if not exists crypto_tax.reconciliation_exceptions (
      run_id text not null,
      exception_id text not null,
      payload jsonb not null,
      primary key (run_id, exception_id)
    );
    """
    with conn.cursor() as cur:
        cur.execute(ddl)


def insert_json_rows(
    conn: Any,
    table: str,
    run_id: str,
    rows: Iterable[Dict[str, Any]],
    key_field: str,
) -> int:
    allowed_tables = {
        "normalized_1099da",
        "resolved_lots",
        "reconciliation_exceptions",
    }
    if table not in allowed_tables:
        raise ValueError(f"Unexpected table: {table}")
    if key_field not in {"record_id", "exception_id"}:
        raise ValueError(f"Unexpected key field: {key_field}")

    count = 0
    with conn.cursor() as cur:
        for row in rows:
            row_key = str(row.get(key_field) or row.get("record_id") or row.get("id") or f"row_{count}")
            cur.execute(
                f"""
                insert into crypto_tax.{table} (run_id, {key_field}, payload)
                values (%s, %s, %s::jsonb)
                on conflict (run_id, {key_field}) do update set payload = excluded.payload
                """,
                (run_id, row_key, json.dumps(row)),
            )
            count += 1
    return count


def persist_artifacts(
    run_id: str,
    normalized: List[Dict[str, Any]],
    resolved: List[Dict[str, Any]],
    exceptions: List[Dict[str, Any]],
    summary: Dict[str, Any],
    input_1099da_path: str,
    input_tax_path: str,
) -> Dict[str, Any]:
    api_key = os.getenv("SEREN_API_KEY")
    if not api_key:
        raise ValueError("SEREN_API_KEY is required")

    api = SerenApi(api_key=api_key)
    target = api.resolve_target()
    conn_str = _patch_dbname(target.connection_string, target.database_name)

    psycopg = _require_psycopg()
    with psycopg.connect(conn_str) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into crypto_tax.reconciliation_runs (run_id, input_1099da_path, input_tax_path, summary)
                values (%s, %s, %s, %s::jsonb)
                on conflict (run_id) do update set
                  input_1099da_path = excluded.input_1099da_path,
                  input_tax_path = excluded.input_tax_path,
                  summary = excluded.summary
                """,
                (run_id, input_1099da_path, input_tax_path, json.dumps(summary)),
            )

        normalized_count = insert_json_rows(conn, "normalized_1099da", run_id, normalized, "record_id")
        resolved_count = insert_json_rows(conn, "resolved_lots", run_id, resolved, "record_id")

        shaped_exceptions = []
        for idx, item in enumerate(exceptions):
            exception_id = str(item.get("id") or item.get("exception_id") or f"exception_{idx}")
            shaped_exceptions.append({"exception_id": exception_id, **item})
        exception_count = insert_json_rows(
            conn,
            "reconciliation_exceptions",
            run_id,
            shaped_exceptions,
            "exception_id",
        )

        conn.commit()

    return {
        "project_id": target.project_id,
        "branch_id": target.branch_id,
        "database": target.database_name,
        "tables": [
            "crypto_tax.reconciliation_runs",
            "crypto_tax.normalized_1099da",
            "crypto_tax.resolved_lots",
            "crypto_tax.reconciliation_exceptions",
        ],
        "counts": {
            "normalized_1099da": normalized_count,
            "resolved_lots": resolved_count,
            "reconciliation_exceptions": exception_count,
        },
    }

