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

    `get_connection_uri` takes the target database name because the
    live publisher's branch-scoped connection-string endpoint returns
    a URI rooted at the branch's default database, not the bootstrap-
    requested one. The HTTP client substitutes the requested name
    into the URI's database segment so callers connect to the right
    schema on the first try (issue #855).
    """

    def list_projects(self) -> list[dict]: ...

    def create_project(self, name: str) -> dict: ...

    def list_databases(self, project_id: str) -> list[dict]: ...

    def create_database(self, project_id: str, name: str) -> dict: ...

    def get_connection_uri(self, project_id: str, database_name: str) -> str: ...


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

    uri = client.get_connection_uri(project["id"], database_name)
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

    The publisher is branch-scoped: databases and connection strings
    hang off `/projects/{project_id}/branches/{branch_id}/...`. The
    client caches each project's `default_branch_id` (returned inline
    on both list_projects and create_project) so callers never have
    to thread a branch id through the Protocol (issue #855).

    The client is single-process; stash state lives on the instance
    and is recreated per bootstrap pass.
    """

    PUBLISHER = "seren-db"

    def __init__(self, *, region: str = _DEFAULT_REGION) -> None:
        self._region = region
        self._default_branch: dict[str, str] = {}

    # ---- Protocol surface --------------------------------------------- #

    def list_projects(self) -> list[dict]:
        resp = self._call("GET", "/projects")
        items = _coerce_list(resp, ("projects", "data", "items"))
        for item in items:
            self._cache_default_branch(item)
        return [_normalize_project(item) for item in items]

    def create_project(self, name: str) -> dict:
        resp = self._call(
            "POST",
            "/projects",
            body={"name": name, "region": self._region},
        )
        self._cache_default_branch(resp)
        return _normalize_project(resp)

    def list_databases(self, project_id: str) -> list[dict]:
        branch_id = self._require_branch(project_id)
        resp = self._call(
            "GET",
            f"/projects/{project_id}/branches/{branch_id}/databases",
        )
        return [
            _normalize_database(item)
            for item in _coerce_list(resp, ("databases", "data", "items"))
        ]

    def create_database(self, project_id: str, name: str) -> dict:
        branch_id = self._require_branch(project_id)
        resp = self._call(
            "POST",
            f"/projects/{project_id}/branches/{branch_id}/databases",
            body={"name": name},
        )
        return _normalize_database(resp)

    def get_connection_uri(self, project_id: str, database_name: str) -> str:
        branch_id = self._require_branch(project_id)
        # `database=` is passed as a query param so a future gateway
        # version that honours it lands on the right database without
        # the substitution step. The current gateway ignores it and
        # always returns the branch's default database URI, so we
        # substitute below as a belt-and-braces measure.
        resp = self._call(
            "GET",
            (
                f"/projects/{project_id}/branches/{branch_id}"
                f"/connection-string?database={database_name}"
            ),
        )
        uri = _extract_connection_uri(resp)
        return _substitute_database_in_uri(uri, database_name) if uri else ""

    # ---- Internals ---------------------------------------------------- #

    def _cache_default_branch(self, project_obj) -> None:
        if not isinstance(project_obj, dict):
            return
        project_id = project_obj.get("id")
        branch_id = project_obj.get("default_branch_id")
        if isinstance(project_id, str) and isinstance(branch_id, str):
            self._default_branch[project_id] = branch_id

    def _require_branch(self, project_id: str) -> str:
        branch_id = self._default_branch.get(project_id)
        if branch_id:
            return branch_id
        # Fall back to a project fetch when the cache is cold (e.g.
        # the caller skipped `list_projects` and went straight to
        # `list_databases`). The fetch repopulates the cache.
        resp = self._call("GET", f"/projects/{project_id}")
        self._cache_default_branch(resp)
        branch_id = self._default_branch.get(project_id)
        if not branch_id:
            raise RuntimeError(
                f"seren-db: cannot resolve default branch for project "
                f"{project_id!r}; expected `default_branch_id` in the "
                "project payload."
            )
        return branch_id

    # ---- Transport ---------------------------------------------------- #

    def _call(self, method: str, path: str, *, body: dict | None = None):
        from scripts import seren_client  # noqa: PLC0415

        return seren_client.call_publisher(
            self.PUBLISHER,
            method,
            path,
            body=body,
        )


def _coerce_list(resp, wrapper_keys: tuple[str, ...]) -> list[dict]:
    if isinstance(resp, list):
        return [item for item in resp if isinstance(item, dict)]
    if isinstance(resp, dict):
        for key in wrapper_keys:
            value = resp.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_connection_uri(resp) -> str:
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        for key in (
            "connection_string",
            "connection_uri",
            "uri",
            "url",
        ):
            value = resp.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _substitute_database_in_uri(uri: str, database_name: str) -> str:
    """Rewrite the database segment of a PostgreSQL URI.

    The publisher's branch-scoped `connection-string` returns a URI
    rooted at the branch's default database (`/serendb`). Bootstrap
    creates a dedicated database (`pk_lead_enrichment`) and wants the
    ledger connection to land there directly. Without this step the
    URI silently writes to the wrong database, which is a P0
    data-routing class of bug.

    Surgically replaces the path segment between the host and the
    query string. URIs without a database path or query string are
    returned unchanged — the caller surfaces an error if needed.
    """

    if not uri or "/" not in uri:
        return uri
    # Split off query / fragment
    head, sep, tail = uri.partition("?")
    # Find the last "/" that begins the database segment.
    last_slash = head.rfind("/")
    # The URI must have a userinfo / host segment before the database.
    # `postgresql://user:pass@host/dbname` — the first "://" delimits
    # the scheme. We need a "/" after the host, not the one inside the
    # scheme separator.
    scheme_end = head.find("://")
    if scheme_end == -1 or last_slash <= scheme_end + 2:
        return uri
    rewritten = head[: last_slash + 1] + database_name
    return rewritten + (sep + tail if sep else "")


def _normalize_project(item) -> dict:
    if not isinstance(item, dict):
        return {}
    project_id = item.get("id") or item.get("project_id") or ""
    return {"id": project_id, "name": item.get("name", "")}


def _normalize_database(item) -> dict:
    if not isinstance(item, dict):
        return {}
    db_id = item.get("id") or item.get("database_id") or ""
    return {"id": db_id, "name": item.get("name", "")}
