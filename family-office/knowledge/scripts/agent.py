#!/usr/bin/env python3
"""Family-office knowledge skill runtime with live Seren connectors."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import requests

from affiliates_webhook import fire_reward_webhook

DEFAULT_DRY_RUN = True
DEFAULT_API_BASE = "https://api.serendb.com"
AVAILABLE_CONNECTORS = ["asana", "docreader", "sharepoint", "storage"]
CAPTURE_COMMANDS = {"capture", "start", "start_capture", "knowledge_capture"}
RETRIEVE_COMMANDS = {"retrieve", "recall", "search", "what_did_i_say_about"}
BRIEF_COMMANDS = {"brief", "show_brief", "show_the_current_working_brief"}
DIFF_COMMANDS = {"diff", "compare", "what_changed_since_the_first_brief"}
STORAGE_COLLECTIONS = {
    "briefs",
    "knowledge_entries",
    "retrieval_events",
    "reward_audits",
    "transcripts",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run family-office knowledge skill runtime.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    return parser.parse_args()


def load_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _current_run_date(config: dict[str, Any]) -> date:
    inputs = config.get("inputs", {})
    for candidate in (
        config.get("current_date"),
        inputs.get("current_date"),
        inputs.get("current_date_label"),
    ):
        parsed = _parse_iso_date(candidate)
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc).date()


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text or "item"


def _text_blob(payload: Any) -> str:
    if isinstance(payload, dict):
        return " ".join(_text_blob(value) for value in payload.values())
    if isinstance(payload, list):
        return " ".join(_text_blob(item) for item in payload)
    return str(payload)


def _tokenize(text: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", text.lower()) if part]


def _topic_terms(topic: str) -> set[str]:
    return set(_tokenize(topic))


def _match_score(record: dict[str, Any], query_text: str) -> int:
    query_terms = _topic_terms(query_text)
    if not query_terms:
        return 1
    haystack = set(_tokenize(_text_blob(record)))
    return len(query_terms & haystack)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _to_json_text(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def _coerce_body(response: requests.Response) -> Any:
    payload = response.json() if response.content else {}
    if isinstance(payload, dict) and "body" in payload:
        return payload["body"]
    return payload


def _ensure_success(response: requests.Response, context: str) -> Any:
    if response.status_code >= 400:
        raise RuntimeError(f"{context} failed: {response.status_code} {response.text}")
    return _coerce_body(response)


def _is_live_mode(config: dict[str, Any]) -> bool:
    if config.get("live_connectors") is not None:
        return bool(config.get("live_connectors"))
    storage_cfg = config.get("storage", {})
    if storage_cfg.get("mode") == "serendb" or str(storage_cfg.get("connection_string", "")).strip():
        return True
    return False


class StorageConnector:
    """In-memory storage adapter used as a local fallback."""

    def __init__(self, records: dict[str, list[dict[str, Any]]] | None):
        self.records = deepcopy(records or {})

    def query(
        self,
        collection: str,
        *,
        filters: dict[str, Any] | None = None,
        query_text: str = "",
        top_k: int | None = None,
        descending: bool = True,
        sort_field: str = "updated_at",
    ) -> dict[str, Any]:
        items = [deepcopy(item) for item in self.records.get(collection, [])]
        filters = filters or {}
        filtered = [item for item in items if all(item.get(key) == value for key, value in filters.items())]
        if query_text:
            scored: list[tuple[int, dict[str, Any]]] = []
            for item in filtered:
                score = _match_score(item, query_text)
                if score > 0:
                    candidate = deepcopy(item)
                    candidate["match_score"] = score
                    scored.append((score, candidate))
            scored.sort(key=lambda pair: (pair[0], pair[1].get(sort_field, "")), reverse=True)
            filtered = [item for _, item in scored]
        else:
            filtered.sort(key=lambda item: item.get(sort_field, ""), reverse=descending)
        if top_k is not None:
            filtered = filtered[:top_k]
        return {
            "status": "ok",
            "connector": "storage",
            "action": "query",
            "collection": collection,
            "records": filtered,
        }

    def upsert(self, collection: str, record: dict[str, Any]) -> dict[str, Any]:
        items = self.records.setdefault(collection, [])
        record_id = record.get("id")
        updated = False
        if record_id:
            for index, existing in enumerate(items):
                if existing.get("id") == record_id:
                    items[index] = deepcopy(record)
                    updated = True
                    break
        if not updated:
            items.append(deepcopy(record))
        return {
            "status": "ok",
            "connector": "storage",
            "action": "upsert",
            "collection": collection,
            "record": deepcopy(record),
            "updated": updated,
        }

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return deepcopy(self.records)

    def close(self) -> None:
        return None


class GatewayClient:
    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key or os.getenv("SEREN_API_KEY")
        if not self.api_key:
            raise ValueError("SEREN_API_KEY is required for live connector mode")
        self.api_base = (api_base or os.getenv("SEREN_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.api_base}{path}"
        response = self.session.request(
            method=method,
            url=url,
            json=body if isinstance(body, (dict, list)) else None,
            data=body if isinstance(body, str) else None,
            params=params,
            headers=headers,
            timeout=60,
        )
        return _ensure_success(response, f"{method} {path}")

    def publisher(
        self,
        publisher: str,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self.request(method, f"/publishers/{publisher}{path}", body=body, headers=headers)

    def list_projects(self) -> list[dict[str, Any]]:
        payload = self.request("GET", "/projects")
        data = payload.get("data", payload)
        return data if isinstance(data, list) else []

    def create_project(self, *, name: str, region: str) -> dict[str, Any]:
        payload = self.request("POST", "/projects", body={"name": name, "region": region})
        return payload.get("data", payload)

    def delete_project(self, project_id: str) -> None:
        self.request("DELETE", f"/projects/{project_id}")

    def list_branches(self, project_id: str) -> list[dict[str, Any]]:
        payload = self.request("GET", f"/projects/{project_id}/branches")
        data = payload.get("data", payload)
        return data if isinstance(data, list) else []

    def list_databases(self, project_id: str, branch_id: str) -> list[dict[str, Any]]:
        payload = self.request("GET", f"/projects/{project_id}/branches/{branch_id}/databases")
        data = payload.get("data", payload)
        return data if isinstance(data, list) else []

    def create_database(self, project_id: str, branch_id: str, name: str) -> dict[str, Any]:
        payload = self.request("POST", f"/projects/{project_id}/branches/{branch_id}/databases", body={"name": name})
        return payload.get("data", payload)

    def get_connection_string(self, project_id: str, branch_id: str, *, role: str = "serendb_owner") -> str:
        payload = self.request(
            "GET",
            f"/projects/{project_id}/branches/{branch_id}/connection-string",
            params={"role": role, "pooled": "false"},
        )
        data = payload.get("data", payload)
        connection_string = data.get("connection_string") if isinstance(data, dict) else None
        if not connection_string:
            raise RuntimeError("Could not resolve SerenDB connection string")
        return str(connection_string)


class SharePointConnector:
    def __init__(self, items: list[dict[str, Any]] | None = None, *, client: GatewayClient | None = None, config: dict[str, Any] | None = None):
        self.items = deepcopy(items or [])
        self.client = client
        self.config = config or {}

    def get(self, *, department: str, topic: str) -> dict[str, Any]:
        if self.client is None:
            scoped = [
                item
                for item in self.items
                if (not department or item.get("department") in ("", department, None))
                and (_match_score(item, topic) > 0 or not topic)
            ]
            return {"status": "ok", "connector": "sharepoint", "action": "get", "records": scoped}

        site_path = self.config.get("site_path", "/sites/root")
        site = self.client.publisher("microsoft-sharepoint", "GET", site_path)
        site_id = str(site.get("id") or "root")
        drive_path = self.config.get("drive_path") or f"/sites/{site_id}/drive"
        drive = self.client.publisher("microsoft-sharepoint", "GET", drive_path)
        children_path = self.config.get("children_path") or f"/drives/{drive['id']}/root/children"
        children = self.client.publisher("microsoft-sharepoint", "GET", children_path)
        records = [
            {
                "id": str(item.get("id", "")),
                "title": str(item.get("name") or item.get("webUrl") or "sharepoint-item"),
                "summary": str(item.get("webUrl") or item.get("createdDateTime") or "SharePoint item"),
                "web_url": item.get("webUrl"),
                "department": department,
                "topic": topic,
            }
            for item in children.get("value", [])
        ]
        if not records:
            records.append(
                {
                    "id": site_id,
                    "title": str(site.get("displayName") or "SharePoint site"),
                    "summary": str(site.get("webUrl") or ""),
                    "web_url": site.get("webUrl"),
                    "department": department,
                    "topic": topic,
                }
            )
        filtered = [record for record in records if _match_score(record, topic) > 0 or not topic] or records[:3]
        return {"status": "ok", "connector": "sharepoint", "action": "get", "records": filtered}


class AsanaConnector:
    def __init__(self, items: list[dict[str, Any]] | None = None, *, client: GatewayClient | None = None, config: dict[str, Any] | None = None):
        self.items = deepcopy(items or [])
        self.client = client
        self.config = config or {}

    def get(self, *, department: str, topic: str) -> dict[str, Any]:
        if self.client is None:
            scoped = [
                item
                for item in self.items
                if (not department or item.get("department") in ("", department, None))
                and (_match_score(item, topic) > 0 or not topic)
            ]
            return {"status": "ok", "connector": "asana", "action": "get", "records": scoped}

        workspace_gid = self.config.get("workspace_gid")
        if not workspace_gid:
            workspaces = self.client.publisher("asana", "GET", "/workspaces")
            workspace_gid = str((workspaces.get("data") or [{}])[0].get("gid") or "")
        if not workspace_gid:
            return {"status": "ok", "connector": "asana", "action": "get", "records": []}

        query = urllib.parse.quote(topic or department or "", safe="")
        limit = int(self.config.get("limit", 5))
        search_path = f"/workspaces/{workspace_gid}/tasks/search?text={query}&limit={limit}"
        payload = self.client.publisher("asana", "GET", search_path)
        records = [
            {
                "id": str(item.get("gid", "")),
                "title": str(item.get("name") or "asana-task"),
                "summary": str(item.get("notes") or item.get("resource_type") or "Asana task"),
                "permalink_url": item.get("permalink_url"),
                "department": department,
                "topic": topic,
            }
            for item in payload.get("data", [])
        ]
        return {"status": "ok", "connector": "asana", "action": "get", "records": records}


class DocReaderConnector:
    def __init__(self, documents: list[dict[str, Any]] | None = None, *, client: GatewayClient | None = None):
        self.documents = deepcopy(documents or [])
        self.client = client

    def post(self, *, topic: str) -> dict[str, Any]:
        records = []
        if self.client is None:
            for item in self.documents:
                if _match_score(item, topic) > 0 or not topic:
                    record = deepcopy(item)
                    record["extracted_text"] = item.get("text", "")
                    records.append(record)
            return {"status": "ok", "connector": "docreader", "action": "post", "records": records}

        for index, item in enumerate(self.documents, start=1):
            encoded = item.get("file")
            if not encoded:
                continue
            payload = self.client.publisher("seren-docreader", "POST", "/process", body={"file": encoded})
            extracted_text = str(payload.get("content", {}).get("text", "")).strip()
            record = {
                "id": str(item.get("id") or f"doc-{index}"),
                "title": str(item.get("title") or f"document-{index}"),
                "summary": extracted_text[:280],
                "extracted_text": extracted_text,
                "file_type": payload.get("file_type"),
                "metadata": payload.get("metadata", {}),
            }
            if _match_score(record, topic) > 0 or not topic:
                records.append(record)
        return {"status": "ok", "connector": "docreader", "action": "post", "records": records}


class LiveStorageConnector:
    def __init__(self, config: dict[str, Any], request: dict[str, Any], gateway: GatewayClient):
        self.config = config
        self.request = request
        self.gateway = gateway
        self.storage_cfg = config.get("storage", {})
        self.project_name = self.storage_cfg.get("project_name") or f"knowledge-live-{_slug(request['organization_name'] or 'org')}"
        self.database_name = self.storage_cfg.get("database_name") or "knowledge"
        self.region = self.storage_cfg.get("region", "us-east-1")
        self.project_id = str(self.storage_cfg.get("project_id", ""))
        self.branch_id = str(self.storage_cfg.get("branch_id", ""))
        direct_connection_string = str(self.storage_cfg.get("connection_string", "")).strip()
        if direct_connection_string:
            self.connection_string = direct_connection_string
        else:
            self.project_id, self.branch_id = self._resolve_project_and_branch()
            self.connection_string = self._resolve_connection_string()
        self.conn = psycopg.connect(self.connection_string)
        self._ensure_schema()

    def _resolve_project_and_branch(self) -> tuple[str, str]:
        project_id = self.storage_cfg.get("project_id")
        branch_id = self.storage_cfg.get("branch_id")
        project = None
        if project_id:
            projects = self.gateway.list_projects()
            project = next((item for item in projects if str(item.get("id")) == str(project_id)), None)
        if project is None:
            for item in self.gateway.list_projects():
                if str(item.get("name", "")).lower() == self.project_name.lower():
                    project = item
                    break
        if project is None:
            project = self.gateway.create_project(name=self.project_name, region=self.region)
        project_id = str(project.get("id"))
        branches = self.gateway.list_branches(project_id)
        branch = None
        if branch_id:
            branch = next((item for item in branches if str(item.get("id")) == str(branch_id)), None)
        if branch is None:
            default_branch_id = project.get("default_branch_id")
            if default_branch_id:
                branch = next((item for item in branches if str(item.get("id")) == str(default_branch_id)), None)
        if branch is None:
            branch = next((item for item in branches if str(item.get("name", "")).lower() in {"main", "production"}), None)
        if branch is None and branches:
            branch = branches[0]
        if branch is None:
            raise RuntimeError(f"No branch available for project {project_id}")
        return project_id, str(branch.get("id"))

    def _resolve_connection_string(self) -> str:
        databases = self.gateway.list_databases(self.project_id, self.branch_id)
        database_names = {str(item.get("name")) for item in databases if item.get("name")}
        if self.database_name not in database_names:
            self.gateway.create_database(self.project_id, self.branch_id, self.database_name)
        conn = self.gateway.get_connection_string(self.project_id, self.branch_id)
        parsed = urllib.parse.urlparse(conn)
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, f"/{self.database_name}", parsed.params, parsed.query, parsed.fragment))

    def _ensure_schema(self) -> None:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_records (
                        collection TEXT NOT NULL,
                        id TEXT NOT NULL,
                        organization_name TEXT,
                        department TEXT,
                        topic TEXT,
                        owner_id TEXT,
                        requester_id TEXT,
                        access_scope TEXT,
                        title TEXT,
                        headline TEXT,
                        summary TEXT,
                        content TEXT,
                        source TEXT,
                        stale_after_days INTEGER,
                        created_at DATE,
                        updated_at DATE,
                        payload JSONB NOT NULL,
                        PRIMARY KEY (collection, id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_knowledge_records_collection_org_dept
                        ON knowledge_records (collection, organization_name, department);
                    """
                )
            self.conn.commit()
        except psycopg.errors.UniqueViolation:
            self.conn.rollback()

    def query(
        self,
        collection: str,
        *,
        filters: dict[str, Any] | None = None,
        query_text: str = "",
        top_k: int | None = None,
        descending: bool = True,
        sort_field: str = "updated_at",
    ) -> dict[str, Any]:
        if collection not in STORAGE_COLLECTIONS:
            return {"status": "ok", "connector": "storage", "action": "query", "collection": collection, "records": []}
        filters = filters or {}
        order_direction = "DESC" if descending else "ASC"
        order_column = "updated_at" if sort_field not in {"created_at", "updated_at"} else sort_field
        clauses = ["collection = %s"]
        params: list[Any] = [collection]
        for field in ("organization_name", "department", "topic", "owner_id", "requester_id"):
            value = filters.get(field)
            if value is not None:
                clauses.append(f"{field} = %s")
                params.append(value)
        if query_text:
            clauses.append("(COALESCE(title, '') || ' ' || COALESCE(summary, '') || ' ' || COALESCE(content, '') || ' ' || COALESCE(topic, '')) ILIKE %s")
            params.append(f"%{query_text}%")
        limit_clause = ""
        if top_k is not None:
            limit_clause = " LIMIT %s"
            params.append(top_k)
        sql = f"""
            SELECT payload
            FROM knowledge_records
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_column} {order_direction}, id ASC
            {limit_clause}
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return {
            "status": "ok",
            "connector": "storage",
            "action": "query",
            "collection": collection,
            "records": [deepcopy(row[0]) for row in rows],
        }

    def upsert(self, collection: str, record: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(record)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO knowledge_records (
                    collection, id, organization_name, department, topic, owner_id, requester_id,
                    access_scope, title, headline, summary, content, source, stale_after_days,
                    created_at, updated_at, payload
                ) VALUES (
                    %(collection)s, %(id)s, %(organization_name)s, %(department)s, %(topic)s, %(owner_id)s, %(requester_id)s,
                    %(access_scope)s, %(title)s, %(headline)s, %(summary)s, %(content)s, %(source)s, %(stale_after_days)s,
                    %(created_at)s, %(updated_at)s, %(payload)s::jsonb
                )
                ON CONFLICT (collection, id) DO UPDATE SET
                    organization_name = EXCLUDED.organization_name,
                    department = EXCLUDED.department,
                    topic = EXCLUDED.topic,
                    owner_id = EXCLUDED.owner_id,
                    requester_id = EXCLUDED.requester_id,
                    access_scope = EXCLUDED.access_scope,
                    title = EXCLUDED.title,
                    headline = EXCLUDED.headline,
                    summary = EXCLUDED.summary,
                    content = EXCLUDED.content,
                    source = EXCLUDED.source,
                    stale_after_days = EXCLUDED.stale_after_days,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    payload = EXCLUDED.payload
                """,
                {
                    "collection": collection,
                    "id": str(record.get("id")),
                    "organization_name": record.get("organization_name"),
                    "department": record.get("department"),
                    "topic": record.get("topic"),
                    "owner_id": record.get("owner_id"),
                    "requester_id": record.get("requester_id"),
                    "access_scope": record.get("access_scope"),
                    "title": record.get("title"),
                    "headline": record.get("headline"),
                    "summary": record.get("summary"),
                    "content": record.get("content"),
                    "source": record.get("source"),
                    "stale_after_days": record.get("stale_after_days"),
                    "created_at": _parse_iso_date(record.get("created_at")),
                    "updated_at": _parse_iso_date(record.get("updated_at")),
                    "payload": _to_json_text(payload),
                },
            )
        self.conn.commit()
        return {
            "status": "ok",
            "connector": "storage",
            "action": "upsert",
            "collection": collection,
            "record": payload,
            "updated": True,
        }

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        snapshot: dict[str, list[dict[str, Any]]] = {}
        for collection in sorted(STORAGE_COLLECTIONS):
            snapshot[collection] = self.query(collection)["records"]
        snapshot["_meta"] = [
            {
                "project_id": self.project_id,
                "branch_id": self.branch_id,
                "database_name": self.database_name,
            }
        ]
        return snapshot

    def close(self) -> None:
        self.conn.close()


def _build_storage(config: dict[str, Any], request: dict[str, Any]) -> StorageConnector | LiveStorageConnector:
    if _is_live_mode(config):
        gateway = GatewayClient()
        return LiveStorageConnector(config, request, gateway)
    return StorageConnector(config.get("storage_records"))


def _build_connectors(
    config: dict[str, Any],
) -> tuple[SharePointConnector, AsanaConnector, DocReaderConnector]:
    if _is_live_mode(config):
        gateway = GatewayClient()
        return (
            SharePointConnector(client=gateway, config=config.get("sharepoint", {})),
            AsanaConnector(client=gateway, config=config.get("asana", {})),
            DocReaderConnector(config.get("documents"), client=gateway),
        )
    return (
        SharePointConnector(config.get("sharepoint_context")),
        AsanaConnector(config.get("asana_context")),
        DocReaderConnector(config.get("documents")),
    )


def normalize_request(config: dict[str, Any]) -> dict[str, Any]:
    inputs = config.get("inputs", {})
    raw_command = str(inputs.get("command", "capture")).strip().lower()
    if raw_command in CAPTURE_COMMANDS:
        mode = "capture"
    elif raw_command in RETRIEVE_COMMANDS:
        mode = "retrieve"
    elif raw_command in BRIEF_COMMANDS:
        mode = "brief"
    elif raw_command in DIFF_COMMANDS:
        mode = "diff"
    else:
        mode = raw_command or "capture"
    query_text = str(inputs.get("query_text", "")).strip()
    requester_id = str(inputs.get("requester_id", config.get("agent_id", "unknown-user"))).strip() or "unknown-user"
    department = str(inputs.get("department", "")).strip()
    organization = str(inputs.get("organization_name", "")).strip()
    topic = query_text or str(inputs.get("topic", "")).strip() or department or organization or "general"
    top_k = int(inputs.get("top_k", 5))
    return {
        "mode": mode,
        "command": raw_command,
        "organization_name": organization,
        "department": department,
        "topic": topic,
        "query_text": query_text,
        "requester_id": requester_id,
        "access_scope": str(inputs.get("access_scope", "team")).strip() or "team",
        "role_template": str(inputs.get("role_template", "general")).strip() or "general",
        "interview_mode": str(inputs.get("interview_mode", "freeform")).strip() or "freeform",
        "top_k": top_k,
        "current_date": _current_run_date(config).isoformat(),
        "run_id": str(config.get("run_id") or uuid4()),
    }


def load_current_brief(storage: StorageConnector | LiveStorageConnector, request: dict[str, Any]) -> dict[str, Any]:
    response = storage.query(
        "briefs",
        filters={"organization_name": request["organization_name"], "department": request["department"]},
        top_k=1,
    )
    records = response["records"]
    return records[0] if records else {}


def sync_sharepoint_context(connector: SharePointConnector, request: dict[str, Any], enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "connector": "sharepoint", "action": "get", "records": []}
    return connector.get(department=request["department"], topic=request["topic"])


def sync_asana_context(connector: AsanaConnector, request: dict[str, Any], enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "connector": "asana", "action": "get", "records": []}
    return connector.get(department=request["department"], topic=request["topic"])


def extract_document_text(connector: DocReaderConnector, request: dict[str, Any]) -> dict[str, Any]:
    return connector.post(topic=request["topic"])


def conduct_guided_interview(config: dict[str, Any], request: dict[str, Any], extracted_documents: dict[str, Any]) -> dict[str, Any]:
    if request["mode"] != "capture":
        return {"status": "skipped", "mode": request["interview_mode"], "turns": [], "turn_count": 0}
    raw_turns = config.get("interview_transcript") or []
    turns: list[dict[str, str]] = []
    for index, raw_turn in enumerate(raw_turns, start=1):
        if isinstance(raw_turn, dict):
            speaker = str(raw_turn.get("speaker", "user"))
            text = str(raw_turn.get("text", "")).strip()
        else:
            speaker = "user" if index % 2 else "assistant"
            text = str(raw_turn).strip()
        if text:
            turns.append({"speaker": speaker, "text": text})
    if not turns and request["query_text"]:
        turns.append({"speaker": "user", "text": request["query_text"]})
    if not turns:
        for document in extracted_documents.get("records", [])[:1]:
            doc_text = str(document.get("extracted_text", "")).strip()
            if doc_text:
                turns.append({"speaker": "user", "text": doc_text})
    summary = " ".join(turn["text"] for turn in turns[:3]).strip()
    return {"status": "ok", "mode": request["interview_mode"], "turns": turns, "turn_count": len(turns), "summary": summary}


def distill_knowledge_entries(
    config: dict[str, Any],
    request: dict[str, Any],
    transcript: dict[str, Any],
    sharepoint_context: dict[str, Any],
    asana_context: dict[str, Any],
    extracted_documents: dict[str, Any],
) -> list[dict[str, Any]]:
    if request["mode"] != "capture":
        return []
    current_date = request["current_date"]
    base_entry = {
        "organization_name": request["organization_name"],
        "department": request["department"],
        "topic": request["topic"],
        "owner_id": request["requester_id"],
        "access_scope": request["access_scope"],
        "created_at": current_date,
        "updated_at": current_date,
        "stale_after_days": int(config.get("freshness_window_days", 30)),
    }
    entries: list[dict[str, Any]] = []
    for index, turn in enumerate(transcript.get("turns", []), start=1):
        if turn["speaker"] != "user":
            continue
        text = turn["text"].strip()
        if not text:
            continue
        entries.append(
            {
                **base_entry,
                "id": f"knowledge-{_slug(request['topic'])}-{request['run_id'][:8]}-{index}",
                "title": f"{request['topic']} note {index}",
                "summary": text,
                "content": text,
                "source": "guided_interview",
            }
        )
    for source_name, payload in (("sharepoint", sharepoint_context), ("asana", asana_context), ("docreader", extracted_documents)):
        for offset, record in enumerate(payload.get("records", [])[:2], start=1):
            text = str(record.get("summary") or record.get("text") or record.get("extracted_text") or "").strip()
            if not text:
                continue
            entries.append(
                {
                    **base_entry,
                    "id": f"{source_name}-{_slug(request['topic'])}-{request['run_id'][:8]}-{offset}",
                    "title": str(record.get("title") or f"{source_name} context"),
                    "summary": text,
                    "content": text,
                    "source": source_name,
                }
            )
    deduped: list[dict[str, Any]] = []
    seen = set()
    for entry in entries:
        fingerprint = (entry["source"], entry["summary"])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(entry)
    return deduped


def archive_transcript(storage: StorageConnector | LiveStorageConnector, request: dict[str, Any], transcript: dict[str, Any]) -> dict[str, Any]:
    if request["mode"] != "capture" or not transcript.get("turns"):
        return {"status": "skipped", "connector": "storage", "action": "upsert", "collection": "transcripts"}
    record = {
        "id": f"transcript-{_slug(request['topic'])}-{request['run_id'][:8]}",
        "organization_name": request["organization_name"],
        "department": request["department"],
        "topic": request["topic"],
        "owner_id": request["requester_id"],
        "created_at": request["current_date"],
        "updated_at": request["current_date"],
        "turns": transcript["turns"],
        "summary": transcript.get("summary", ""),
    }
    return storage.upsert("transcripts", record)


def persist_knowledge_entries(storage: StorageConnector | LiveStorageConnector, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [storage.upsert("knowledge_entries", entry) for entry in entries]


def retrieve_candidate_entries(storage: StorageConnector | LiveStorageConnector, request: dict[str, Any]) -> dict[str, Any]:
    if request["mode"] not in {"retrieve", "brief", "diff"}:
        return {"status": "skipped", "connector": "storage", "action": "query", "collection": "knowledge_entries", "records": []}
    return storage.query(
        "knowledge_entries",
        filters={"organization_name": request["organization_name"], "department": request["department"]},
        query_text=request["topic"],
        top_k=request["top_k"],
    )


def _access_allowed(request: dict[str, Any], entry: dict[str, Any]) -> bool:
    scope = entry.get("access_scope", "team")
    if scope == "public":
        return True
    if scope == "private":
        return entry.get("owner_id") == request["requester_id"]
    if scope == "team":
        return entry.get("department") == request["department"]
    return True


def _freshness_status(request: dict[str, Any], entry: dict[str, Any]) -> tuple[str, int | None]:
    current_date = _parse_iso_date(request["current_date"])
    updated_at = _parse_iso_date(entry.get("updated_at") or entry.get("created_at"))
    if current_date is None or updated_at is None:
        return "unknown", None
    age_days = (current_date - updated_at).days
    stale_after_days = int(entry.get("stale_after_days", 30))
    return ("stale" if age_days > stale_after_days else "fresh"), age_days


def apply_access_and_freshness_rules(request: dict[str, Any], candidate_response: dict[str, Any]) -> dict[str, Any]:
    allowed = []
    for entry in candidate_response.get("records", []):
        if not _access_allowed(request, entry):
            continue
        candidate = deepcopy(entry)
        freshness, age_days = _freshness_status(request, candidate)
        candidate["freshness"] = freshness
        candidate["age_days"] = age_days
        allowed.append(candidate)
    return {"status": "ok", "records": allowed, "filtered_out_count": max(0, len(candidate_response.get("records", [])) - len(allowed))}


def compose_answer_or_followup(request: dict[str, Any], filtered_results: dict[str, Any], transcript: dict[str, Any]) -> dict[str, Any]:
    mode = request["mode"]
    if mode == "capture":
        return {
            "status": "ok",
            "kind": "answer",
            "message": f"Captured {transcript.get('turn_count', 0)} interview turns and prepared {len(filtered_results.get('records', []))} knowledge entries for {request['topic']}.",
        }
    records = filtered_results.get("records", [])
    if mode == "retrieve":
        if not records:
            return {
                "status": "needs_input",
                "kind": "followup",
                "message": f"No knowledge entries matched '{request['topic']}'. Provide more detail or run a capture session.",
            }
        highlights = "; ".join(
            f"{record.get('title', 'entry')}: {record.get('summary', '')} [{record.get('freshness', 'unknown')}]"
            for record in records[:3]
        )
        return {"status": "ok", "kind": "answer", "message": highlights}
    if mode == "brief":
        return {"status": "ok", "kind": "answer", "message": "Rendered current working brief."}
    if mode == "diff":
        return {"status": "ok", "kind": "answer", "message": "Compared current brief against the first recorded brief."}
    return {"status": "ok", "kind": "answer", "message": "Request processed."}


def log_retrieval_events(storage: StorageConnector | LiveStorageConnector, request: dict[str, Any], filtered_results: dict[str, Any]) -> dict[str, Any]:
    if request["mode"] != "retrieve":
        return {"status": "skipped", "connector": "storage", "action": "upsert", "collection": "retrieval_events"}
    record = {
        "id": f"retrieval-{_slug(request['topic'])}-{request['run_id'][:8]}",
        "query_text": request["query_text"],
        "topic": request["topic"],
        "requester_id": request["requester_id"],
        "organization_name": request["organization_name"],
        "department": request["department"],
        "created_at": request["current_date"],
        "updated_at": request["current_date"],
        "result_count": len(filtered_results.get("records", [])),
        "result_ids": [entry.get("id") for entry in filtered_results.get("records", [])],
    }
    return storage.upsert("retrieval_events", record)


def calculate_rewards(config: dict[str, Any], event_type: str = "knowledge_capture") -> dict[str, Any]:
    inputs = config.get("inputs", {})
    agent_id = config.get("agent_id", inputs.get("agent_id", ""))
    referral_code = config.get("referral_code", inputs.get("referral_code", ""))
    amount_cents = int(inputs.get("reward_per_retrieval_usd", 1) * 100) if event_type == "knowledge_retrieval" else int(inputs.get("reward_base_usd", 100) * 100)
    test_mode = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    try:
        result = fire_reward_webhook(
            config=config,
            agent_id=agent_id,
            referral_code=referral_code,
            event_type=event_type,
            amount_cents=amount_cents,
            test_mode=test_mode,
        )
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}
    result["event_type"] = event_type
    result["amount_cents"] = amount_cents
    return result


def persist_rewards(storage: StorageConnector | LiveStorageConnector, request: dict[str, Any], reward_result: dict[str, Any]) -> dict[str, Any]:
    status = reward_result.get("status", "unknown")
    tx_id = reward_result.get("transaction_id", "")
    if status == "sent":
        print(f"  Reward webhook sent: transaction_id={tx_id}")
    elif status == "skipped":
        print(f"  Reward webhook skipped: {reward_result.get('reason', '')}")
    elif status == "failed":
        print(f"  Reward webhook failed: {reward_result.get('error', '')} (transaction_id={tx_id})")
    elif status == "error":
        print(f"  Reward webhook error: {reward_result.get('error', '')}")
    record = {
        "id": tx_id or f"reward-{request['mode']}-{request['run_id'][:8]}",
        "request_mode": request["mode"],
        "topic": request["topic"],
        "requester_id": request["requester_id"],
        "created_at": request["current_date"],
        "updated_at": request["current_date"],
        **reward_result,
    }
    storage.upsert("reward_audits", record)
    return reward_result


def _brief_record_summary(brief: dict[str, Any]) -> str:
    if not brief:
        return "No brief available."
    return str(brief.get("summary") or brief.get("headline") or "No brief summary recorded.")


def render_working_brief(
    storage: StorageConnector | LiveStorageConnector,
    request: dict[str, Any],
    current_brief: dict[str, Any],
    sharepoint_context: dict[str, Any],
    asana_context: dict[str, Any],
    extracted_documents: dict[str, Any],
    filtered_results: dict[str, Any],
) -> dict[str, Any]:
    derived_summary = " ".join(entry.get("summary", "") for entry in filtered_results.get("records", [])[:2]).strip()
    brief = {
        "mode": request["mode"],
        "headline": current_brief.get("headline") or f"{request['organization_name']} {request['department']} working brief",
        "summary": current_brief.get("summary") or derived_summary or _brief_record_summary(current_brief),
        "priorities": list(current_brief.get("priorities", [])) or [entry.get("title") for entry in filtered_results.get("records", [])[:3] if entry.get("title")],
        "risks": list(current_brief.get("risks", [])),
        "source_counts": {
            "sharepoint": len(sharepoint_context.get("records", [])),
            "asana": len(asana_context.get("records", [])),
            "documents": len(extracted_documents.get("records", [])),
            "knowledge_entries": len(filtered_results.get("records", [])),
        },
        "notes": [],
    }
    for record in sharepoint_context.get("records", [])[:2]:
        brief["notes"].append(f"SharePoint: {record.get('title', 'context')}")
    for record in asana_context.get("records", [])[:2]:
        brief["notes"].append(f"Asana: {record.get('title', 'task')}")
    for record in extracted_documents.get("records", [])[:1]:
        brief["notes"].append(f"Document: {record.get('title', 'document')}")
    if request["mode"] == "diff":
        earliest = storage.query(
            "briefs",
            filters={"organization_name": request["organization_name"], "department": request["department"]},
            top_k=100,
            descending=False,
            sort_field="created_at",
        )["records"]
        first_brief = earliest[0] if earliest else {}
        changes = []
        if first_brief.get("summary") != current_brief.get("summary"):
            changes.append({"field": "summary", "from": first_brief.get("summary", ""), "to": current_brief.get("summary", "")})
        first_priorities = set(first_brief.get("priorities", []))
        current_priorities = set(current_brief.get("priorities", []))
        for added in sorted(current_priorities - first_priorities):
            changes.append({"field": "priorities", "change": "added", "value": added})
        for removed in sorted(first_priorities - current_priorities):
            changes.append({"field": "priorities", "change": "removed", "value": removed})
        brief["comparison"] = {"first_brief_id": first_brief.get("id", ""), "current_brief_id": current_brief.get("id", ""), "changes": changes}
    return brief


def persist_working_brief_snapshot(storage: StorageConnector | LiveStorageConnector, request: dict[str, Any], working_brief: dict[str, Any]) -> dict[str, Any]:
    if request["mode"] not in {"capture", "brief"}:
        return {"status": "skipped", "connector": "storage", "action": "upsert", "collection": "briefs"}
    record = {
        "id": f"brief-{request['current_date']}-{request['run_id'][:8]}",
        "organization_name": request["organization_name"],
        "department": request["department"],
        "topic": request["topic"],
        "headline": working_brief.get("headline"),
        "summary": working_brief.get("summary"),
        "priorities": working_brief.get("priorities", []),
        "risks": working_brief.get("risks", []),
        "created_at": request["current_date"],
        "updated_at": request["current_date"],
    }
    return storage.upsert("briefs", record)


def run_once(config: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    request = normalize_request(config)
    storage = _build_storage(config, request)
    try:
        sharepoint, asana, docreader = _build_connectors(config)
        current_brief = load_current_brief(storage, request)
        sharepoint_context = sync_sharepoint_context(sharepoint, request, enabled=bool(config.get("inputs", {}).get("sharepoint_sync_enabled", True)))
        asana_context = sync_asana_context(asana, request, enabled=bool(config.get("inputs", {}).get("asana_sync_enabled", True)))
        extracted_documents = extract_document_text(docreader, request)
        transcript = conduct_guided_interview(config, request, extracted_documents)
        knowledge_entries = distill_knowledge_entries(config, request, transcript, sharepoint_context, asana_context, extracted_documents)
        transcript_write = archive_transcript(storage, request, transcript)
        knowledge_writes = persist_knowledge_entries(storage, knowledge_entries)
        candidate_entries = retrieve_candidate_entries(storage, request)
        if request["mode"] == "capture":
            candidate_entries = {"status": "ok", "records": knowledge_entries, "collection": "knowledge_entries", "connector": "storage", "action": "query"}
        filtered_results = apply_access_and_freshness_rules(request, candidate_entries)
        response = compose_answer_or_followup(request, filtered_results, transcript)
        retrieval_log = log_retrieval_events(storage, request, filtered_results)
        if request["mode"] == "capture":
            reward = calculate_rewards(config, event_type="knowledge_capture")
        elif request["mode"] == "retrieve":
            reward = calculate_rewards(config, event_type="knowledge_retrieval")
        else:
            reward = {"status": "skipped", "reason": "reward_not_applicable", "event_type": request["mode"]}
        reward_audit = persist_rewards(storage, request, reward)
        working_brief = render_working_brief(storage, request, current_brief, sharepoint_context, asana_context, extracted_documents, filtered_results)
        brief_write = persist_working_brief_snapshot(storage, request, working_brief)
        return {
            "status": "ok",
            "skill": "knowledge",
            "dry_run": dry_run,
            "connectors": AVAILABLE_CONNECTORS,
            "normalized_request": request,
            "current_brief": current_brief,
            "sharepoint_context": sharepoint_context,
            "asana_context": asana_context,
            "extracted_documents": extracted_documents,
            "transcript": transcript,
            "knowledge_entries": knowledge_entries,
            "transcript_write": transcript_write,
            "knowledge_writes": knowledge_writes,
            "retrieval_candidates": candidate_entries,
            "retrieval_results": filtered_results,
            "response": response,
            "retrieval_log": retrieval_log,
            "reward": reward_audit,
            "working_brief": working_brief,
            "brief_write": brief_write,
            "storage_state": storage.snapshot(),
        }
    finally:
        storage.close()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    result = run_once(config=config, dry_run=dry_run)
    print(json.dumps(result, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
