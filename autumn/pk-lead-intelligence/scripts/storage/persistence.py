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
