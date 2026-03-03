#!/usr/bin/env python3
"""Resolve (and optionally provision) a SerenDB DSN from SEREN_API_KEY."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class SerenBootstrapError(RuntimeError):
    pass


@dataclass
class SerenDbTarget:
    project_id: str
    branch_id: str
    database_name: str
    connection_string: str


class SerenApi:
    def __init__(self, api_key: str, api_base: Optional[str] = None):
        if not api_key:
            raise ValueError("SEREN_API_KEY is required")
        self.api_key = api_key
        self.api_base = (api_base or os.getenv("SEREN_API_BASE") or "https://api.serendb.com").rstrip("/")

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
                return json.loads(payload) if payload else {}
        except Exception as exc:
            raise SerenBootstrapError(f"Seren API request failed ({method} {path}): {exc}") from exc

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
        payload = self._request("POST", f"/projects/{project_id}/branches/{branch_id}/databases", body={"name": name})
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def get_connection_string(self, project_id: str, branch_id: str, role: str = "serendb_owner") -> str:
        payload = self._request(
            "GET",
            f"/projects/{project_id}/branches/{branch_id}/connection-string",
            query={"role": role, "pooled": "false"},
        )
        data = payload.get("data")
        if isinstance(data, dict):
            conn = data.get("connection_string")
            if conn:
                return str(conn)
        conn = payload.get("connection_string")
        if conn:
            return str(conn)
        raise SerenBootstrapError("Could not resolve connection string from Seren API")


def _patch_database(connection_string: str, database_name: str) -> str:
    parsed = urllib.parse.urlparse(connection_string)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, f"/{database_name}", parsed.params, parsed.query, parsed.fragment))


def resolve_or_create_serendb_dsn(
    api_key: str,
    project_name: str = "alpaca-sass-short-trader-delta-neutral",
    database_name: str = "alpaca_sass_short_bot_dn",
    region: str = "aws-us-east-2",
) -> str:
    api = SerenApi(api_key=api_key)

    project_id = os.getenv("SEREN_PROJECT_ID")
    branch_id = os.getenv("SEREN_BRANCH_ID")
    forced_db = os.getenv("SEREN_DATABASE_NAME")
    if forced_db:
        database_name = forced_db

    projects = api.list_projects()
    project: Optional[Dict[str, Any]] = None
    if project_id:
        project = next((p for p in projects if str(p.get("id")) == project_id), None)
    if not project:
        project = next((p for p in projects if str(p.get("name", "")).lower() == project_name.lower()), None)
    if not project:
        project = api.create_project(name=project_name, region=region)

    project_id = str(project.get("id") or "")
    if not project_id:
        raise SerenBootstrapError("Unable to determine project_id")

    branches = api.list_branches(project_id)
    if not branches:
        raise SerenBootstrapError(f"No branches available for project {project_id}")

    branch: Optional[Dict[str, Any]] = None
    if branch_id:
        branch = next((b for b in branches if str(b.get("id")) == branch_id), None)
    if not branch:
        default_branch_id = project.get("default_branch_id") if isinstance(project, dict) else None
        if default_branch_id:
            branch = next((b for b in branches if str(b.get("id")) == str(default_branch_id)), None)
    if not branch:
        branch = next((b for b in branches if str(b.get("name", "")).lower() in {"main", "production"}), None)
    if not branch:
        branch = branches[0]

    branch_id = str(branch.get("id") or "")
    if not branch_id:
        raise SerenBootstrapError("Unable to determine branch_id")

    dbs = api.list_databases(project_id, branch_id)
    db_names = {str(d.get("name")) for d in dbs if d.get("name")}
    if database_name not in db_names:
        api.create_database(project_id=project_id, branch_id=branch_id, name=database_name)

    conn = api.get_connection_string(project_id=project_id, branch_id=branch_id)
    return _patch_database(conn, database_name)


def resolve_dsn(
    dsn: Optional[str],
    api_key: Optional[str],
    project_name: str = "alpaca-sass-short-trader-delta-neutral",
    database_name: str = "alpaca_sass_short_bot_dn",
) -> str:
    if dsn:
        return dsn
    key = api_key or os.getenv("SEREN_API_KEY")
    if not key:
        raise SerenBootstrapError("SEREN_API_KEY is required when --dsn is not provided")
    return resolve_or_create_serendb_dsn(api_key=key, project_name=project_name, database_name=database_name)
