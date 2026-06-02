"""SerenDB persistence layer.

Owns the SerenDB bootstrap (project + database resolution + connection
URI fetch) and — in later phases — the `psycopg2`-backed SQL helpers
that write `enriched_leads`, run history, and weekly status records.

This module is pure orchestration in phase 1: it takes a `SerenDBClient`
implementation and an idempotent (project_name, database_name) pair,
and returns a Postgres connection URI. The concrete HTTP client for
the `seren-db` publisher ships in a follow-up PR.
"""

from __future__ import annotations

from typing import Protocol


class SerenDBClient(Protocol):
    """Subset of the `seren-db` publisher needed by bootstrap.

    The Protocol is structural — any object with these five methods
    satisfies it. Tests inject an in-memory fake; production code
    will inject an HTTP-backed client.
    """

    def list_projects(self) -> list[dict]: ...

    def create_project(self, name: str) -> dict: ...

    def list_databases(self, project_id: str) -> list[dict]: ...

    def create_database(self, project_id: str, name: str) -> dict: ...

    def get_connection_uri(self, project_id: str) -> str: ...


def _find_by_exact_name(items: list[dict], name: str) -> dict | None:
    """Return the first item whose `name` field equals `name` exactly.

    Substring / prefix matches are deliberately rejected — a name
    collision like `pk-lead-intelligence` vs
    `pk-lead-intelligence-staging` would silently send the skill at
    the wrong project, which is a P0 data-loss class of bug.
    """

    for item in items:
        if item.get("name") == name:
            return item
    return None


def bootstrap_serendb(
    *,
    project_name: str,
    database_name: str,
    client: SerenDBClient,
) -> str:
    """Resolve or create the SerenDB project + database, return URI.

    The function is idempotent: a second call against an already-
    bootstrapped client performs the two list calls + the URI fetch
    and exits without creating anything. The first call creates only
    what is missing.

    A freshly-created project is assumed to start empty, so the
    database is created directly without a `list_databases` round-
    trip. This avoids a race where a stale list response could mask a
    successful create-database call and produce a duplicate.
    """

    project = _find_by_exact_name(client.list_projects(), project_name)
    if project is None:
        project = client.create_project(project_name)
        # New project — known to be empty. Create the database
        # directly without re-listing.
        client.create_database(project["id"], database_name)
    else:
        database = _find_by_exact_name(
            client.list_databases(project["id"]),
            database_name,
        )
        if database is None:
            client.create_database(project["id"], database_name)

    uri = client.get_connection_uri(project["id"])
    if not uri:
        raise RuntimeError(
            "SerenDB returned an empty connection URI for project "
            f"{project_name!r} — verify the project exists and the "
            "Service Account has the connection-uri scope"
        )
    return uri


# --------------------------------------------------------------------- #
# HTTP-backed production client                                         #
# --------------------------------------------------------------------- #
#
# The fake `SerenDBClient` lives in the tests. The real one bridges
# the five Protocol methods to the `seren-db` publisher via the
# existing `seren_client.call_publisher` seam, so bootstrap can run
# on operator machines without any extra wiring.

_DEFAULT_REGION = "aws-us-east-2"


class HttpSerenDBClient:
    """Production `SerenDBClient` impl backed by the `seren-db`
    publisher gateway.

    Endpoint shape mirrors the paths covered by `seren_client._build_url`
    tests (`/projects`, `/projects/{id}/...`). The gateway resolves the
    project's default branch when one is not supplied — bootstrap
    targets a fresh project's main branch, so this matches the user
    flow we ship.

    The client is single-process and stateless; it can be reconstructed
    per bootstrap pass.
    """

    PUBLISHER = "seren-db"

    def __init__(self, *, region: str = _DEFAULT_REGION) -> None:
        self._region = region

    # ---- Protocol surface --------------------------------------------- #

    def list_projects(self) -> list[dict]:
        resp = self._call("GET", "/projects")
        return _normalize_project_list(resp)

    def create_project(self, name: str) -> dict:
        resp = self._call(
            "POST",
            "/projects",
            body={"name": name, "region": self._region},
        )
        return _normalize_project(resp)

    def list_databases(self, project_id: str) -> list[dict]:
        resp = self._call("GET", f"/projects/{project_id}/databases")
        return _normalize_database_list(resp)

    def create_database(self, project_id: str, name: str) -> dict:
        resp = self._call(
            "POST",
            f"/projects/{project_id}/databases",
            body={"name": name},
        )
        return _normalize_database(resp)

    def get_connection_uri(self, project_id: str) -> str:
        resp = self._call(
            "GET",
            f"/projects/{project_id}/connection_uri",
        )
        return _normalize_connection_uri(resp)

    # ---- Transport ---------------------------------------------------- #

    def _call(self, method: str, path: str, *, body: dict | None = None):
        from scripts import seren_client  # noqa: PLC0415

        return seren_client.call_publisher(
            self.PUBLISHER,
            method,
            path,
            body=body,
        )


def _normalize_project_list(resp) -> list[dict]:
    """Coerce the gateway response into `[{id, name}, ...]`.

    `seren-db` is a data publisher so `call_publisher` returns the
    unwrapped `data` payload directly. Different gateway versions
    have shipped either a bare list or a wrapper dict — handle both.
    """

    if isinstance(resp, list):
        return [_normalize_project(item) for item in resp]
    if isinstance(resp, dict):
        for key in ("projects", "data", "items"):
            value = resp.get(key)
            if isinstance(value, list):
                return [_normalize_project(item) for item in value]
    return []


def _normalize_project(item) -> dict:
    if not isinstance(item, dict):
        return {}
    # Older paths used `project_id`; current shapes use `id`.
    project_id = item.get("id") or item.get("project_id") or ""
    return {"id": project_id, "name": item.get("name", "")}


def _normalize_database_list(resp) -> list[dict]:
    if isinstance(resp, list):
        return [_normalize_database(item) for item in resp]
    if isinstance(resp, dict):
        for key in ("databases", "data", "items"):
            value = resp.get(key)
            if isinstance(value, list):
                return [_normalize_database(item) for item in value]
    return []


def _normalize_database(item) -> dict:
    if not isinstance(item, dict):
        return {}
    db_id = item.get("id") or item.get("database_id") or ""
    return {"id": db_id, "name": item.get("name", "")}


def _normalize_connection_uri(resp) -> str:
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        for key in (
            "uri",
            "connection_uri",
            "connection_string",
            "url",
        ):
            value = resp.get(key)
            if isinstance(value, str) and value:
                return value
    return ""
