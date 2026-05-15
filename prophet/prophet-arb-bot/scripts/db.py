"""SerenDB Postgres connection plumbing.

The `seren-db` publisher is a Neon-style management API: it provisions
projects, branches, and databases, but it does NOT expose an
ad-hoc `run-sql` HTTP endpoint. SQL execution happens via direct
Postgres connection using the URI returned by
`/publishers/seren-db/projects/{id}/connection_uri`.

This module hides that two-step (resolve URI → open psycopg2 connection)
behind a single `get_connection(project_name, database_name)` call.

Caching:
  - The resolved connection URI is cached per (project, database) for
    the life of the process. URIs include short-lived credentials but
    psycopg2 connections themselves do not refresh on the fly; if a
    cycle takes long enough to exhaust credential lifetime, the next
    cycle will re-resolve.
  - Connections are NOT pooled in v1 — each agent.py invocation opens
    one connection, runs setup or a cycle, and closes. The cron tick
    cadence is hourly, so connection churn is negligible.
"""

from __future__ import annotations

import json
import os
import re
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PUBLISHER_HOST = "https://api.serendb.com"
PUBLISHER_PREFIX = "/publishers/seren-db"
HTTP_TIMEOUT_SECONDS = 20.0
PG_CONNECT_TIMEOUT_SECONDS = 60.0  # Neon-style compute can cold-start


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore[import-not-found]

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


@dataclass
class ResolvedTarget:
    project_id: str
    project_name: str
    branch_id: str
    database_name: str
    connection_uri: str


def _http_get(path: str, *, api_key: str) -> Any:
    return _http_request("GET", path, api_key=api_key, body=None)


def _http_post(path: str, *, api_key: str, body: dict[str, Any]) -> Any:
    return _http_request("POST", path, api_key=api_key, body=body)


def _http_request(
    method: str,
    path: str,
    *,
    api_key: str,
    body: dict[str, Any] | None,
) -> Any:
    data: bytes | None = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(
        f"{PUBLISHER_HOST}{PUBLISHER_PREFIX}{path}",
        headers=headers,
        method=method,
        data=data,
    )
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS, context=_ssl_context()) as r:
            text = r.read().decode("utf-8")
    except HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(
            f"seren-db {method} {path} failed: HTTP {exc.code} body={body_text[:300]}"
        ) from exc
    payload = json.loads(text) if text else {}
    # The seren-db publisher uses two envelope shapes:
    #   - { "data": [ ... ] }                 (list responses, e.g. /projects)
    #   - { "data": { "uri": "..." } }        (dict responses, e.g. /connection_uri)
    # HttpGateway-style runs unwrap one more level via { "data": { "body": ... } }
    # for transport-proxied calls; we tolerate both.
    if isinstance(payload, dict) and "data" in payload:
        inner = payload["data"]
        if isinstance(inner, dict) and "body" in inner:
            return inner["body"]
        return inner
    return payload


def resolve_target(
    *,
    api_key: str,
    project_name: str,
    database_name: str,
) -> ResolvedTarget:
    """Look up project + branch + database and return a Postgres URI.

    Auto-provisions the database on the project's default branch when it
    does not exist yet — `--command setup` is the canonical bootstrap
    surface and operators should not need to drop to a side-channel
    `create_database` MCP call before running it. See issue #573.

    Fails closed (RuntimeError) if any of:
      - project_name is not found in the caller's organization
      - the project has no default branch
      - the auto-create POST fails (operator must address the underlying
        permission or quota issue before retrying)
      - the connection_uri endpoint returns no usable URI
    """
    projects_payload = _http_get("/projects", api_key=api_key)
    projects = (
        projects_payload
        if isinstance(projects_payload, list)
        else (projects_payload.get("projects") if isinstance(projects_payload, dict) else None)
    )
    if not isinstance(projects, list):
        raise RuntimeError(f"seren-db /projects returned unexpected shape: {type(projects).__name__}")

    project = next(
        (p for p in projects if isinstance(p, dict) and p.get("name") == project_name),
        None,
    )
    if project is None:
        raise RuntimeError(
            f"seren-db project '{project_name}' not found in this organization. "
            f"Create it via POST /publishers/seren-db/projects before running setup."
        )
    project_id = str(project.get("id") or "")
    branch_id = str(project.get("default_branch_id") or "")
    if not project_id or not branch_id:
        raise RuntimeError(
            f"project '{project_name}' is missing id or default_branch_id: {project}"
        )

    # Validate the database exists on the default branch.
    databases = _http_get(
        f"/projects/{project_id}/branches/{branch_id}/databases",
        api_key=api_key,
    )
    if not isinstance(databases, list):
        raise RuntimeError(
            f"seren-db databases endpoint returned non-list: {type(databases).__name__}"
        )
    db = next(
        (d for d in databases if isinstance(d, dict) and d.get("name") == database_name),
        None,
    )
    if db is None:
        # Auto-create the database on the project's default branch.
        # Why: setup is the bootstrap surface; requiring a side-channel
        # `seren__create_database` MCP call before setup is exactly the
        # snag #573 reported (operator hung 5–10 min on the error).
        _http_post(
            f"/projects/{project_id}/branches/{branch_id}/databases",
            api_key=api_key,
            body={"name": database_name},
        )
        databases = _http_get(
            f"/projects/{project_id}/branches/{branch_id}/databases",
            api_key=api_key,
        )
        if not isinstance(databases, list):
            raise RuntimeError(
                f"seren-db databases endpoint returned non-list after create: "
                f"{type(databases).__name__}"
            )
        db = next(
            (
                d
                for d in databases
                if isinstance(d, dict) and d.get("name") == database_name
            ),
            None,
        )
        if db is None:
            raise RuntimeError(
                f"database '{database_name}' still missing on project "
                f"'{project_name}' after auto-create POST."
            )

    # Fetch the connection URI. The publisher returns the project's
    # default database name in the URI path; substitute the requested
    # database name in by-position.
    uri_payload = _http_get(
        f"/projects/{project_id}/connection_uri", api_key=api_key
    )
    uri = (
        uri_payload.get("uri") if isinstance(uri_payload, dict) else None
    )
    if not isinstance(uri, str) or not uri.startswith("postgres"):
        raise RuntimeError(f"seren-db connection_uri returned no URI: {uri_payload}")
    uri = re.sub(r"/[^/?]+(?=\?)", f"/{database_name}", uri)
    return ResolvedTarget(
        project_id=project_id,
        project_name=project_name,
        branch_id=branch_id,
        database_name=database_name,
        connection_uri=uri,
    )


_TARGET_CACHE: dict[tuple[str, str], ResolvedTarget] = {}


def get_target(
    *, project_name: str, database_name: str, api_key: str | None = None
) -> ResolvedTarget:
    """Memoized resolve_target. Re-resolves on every fresh process."""
    cache_key = (project_name, database_name)
    cached = _TARGET_CACHE.get(cache_key)
    if cached is not None:
        return cached
    key = (api_key or os.getenv("SEREN_API_KEY") or os.getenv("API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "SEREN_API_KEY (or runtime-injected API_KEY) is required to resolve SerenDB."
        )
    target = resolve_target(
        api_key=key,
        project_name=project_name,
        database_name=database_name,
    )
    _TARGET_CACHE[cache_key] = target
    return target


@contextmanager
def open_connection(target: ResolvedTarget) -> Iterator[Any]:
    """Open a psycopg2 connection scoped to the resolved target.

    Imports psycopg2 lazily so callers that only touch the in-memory
    test paths don't pay the import cost. Live runs require
    `psycopg2-binary` (see requirements.txt).
    """
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "psycopg2 is required for SerenDB persistence. "
            "Install with `pip install psycopg2-binary`."
        ) from exc
    conn = psycopg2.connect(
        target.connection_uri,
        connect_timeout=int(PG_CONNECT_TIMEOUT_SECONDS),
    )
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
