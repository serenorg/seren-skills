"""Unit tests for the SerenDB bootstrap helper.

The bootstrap function orchestrates a `seren-db` client: check-then-
create for the project, check-then-create for the database, then
fetch a Postgres connection URI. The function itself is pure
orchestration — no HTTP, no env, no IO. The concrete HTTP client
lands in a follow-up PR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from scripts.storage import persistence as p


# --------------------------------------------------------------------- #
# Fake client                                                           #
# --------------------------------------------------------------------- #


@dataclass
class FakeSerenDBClient:
    """In-memory implementation of `SerenDBClient` Protocol.

    Records every call so tests can assert ordering and idempotency.
    """

    projects: list[dict] = field(default_factory=list)
    databases: dict[str, list[dict]] = field(default_factory=dict)
    connection_uris: dict[str, str] = field(default_factory=dict)

    list_projects_calls: int = 0
    create_project_calls: list[str] = field(default_factory=list)
    list_databases_calls: list[str] = field(default_factory=list)
    create_database_calls: list[tuple[str, str]] = field(default_factory=list)
    get_connection_uri_calls: list[str] = field(default_factory=list)

    def list_projects(self) -> list[dict]:
        self.list_projects_calls += 1
        return list(self.projects)

    def create_project(self, name: str) -> dict:
        self.create_project_calls.append(name)
        project = {"id": f"prj_{name}", "name": name}
        self.projects.append(project)
        self.databases.setdefault(project["id"], [])
        self.connection_uris.setdefault(
            project["id"],
            f"postgresql://test/{project['id']}",
        )
        return project

    def list_databases(self, project_id: str) -> list[dict]:
        self.list_databases_calls.append(project_id)
        return list(self.databases.get(project_id, []))

    def create_database(self, project_id: str, name: str) -> dict:
        self.create_database_calls.append((project_id, name))
        database = {"id": f"db_{name}", "name": name}
        self.databases.setdefault(project_id, []).append(database)
        return database

    def get_connection_uri(self, project_id: str) -> str:
        self.get_connection_uri_calls.append(project_id)
        return self.connection_uris[project_id]


# --------------------------------------------------------------------- #
# Happy paths                                                           #
# --------------------------------------------------------------------- #


def test_returns_connection_uri_when_project_and_database_exist() -> None:
    client = FakeSerenDBClient(
        projects=[{"id": "prj_existing", "name": "pk-lead-intelligence"}],
        databases={"prj_existing": [{"id": "db_existing", "name": "pk_lead_intelligence"}]},
        connection_uris={"prj_existing": "postgresql://example/pk_lead_intelligence"},
    )

    uri = p.bootstrap_serendb(
        project_name="pk-lead-intelligence",
        database_name="pk_lead_intelligence",
        client=client,
    )

    assert uri == "postgresql://example/pk_lead_intelligence"
    assert client.create_project_calls == []
    assert client.create_database_calls == []


def test_creates_project_when_missing() -> None:
    client = FakeSerenDBClient(projects=[])

    uri = p.bootstrap_serendb(
        project_name="pk-lead-intelligence",
        database_name="pk_lead_intelligence",
        client=client,
    )

    assert client.create_project_calls == ["pk-lead-intelligence"]
    assert client.create_database_calls == [
        ("prj_pk-lead-intelligence", "pk_lead_intelligence"),
    ]
    assert uri == "postgresql://test/prj_pk-lead-intelligence"


def test_creates_database_when_project_exists_but_database_missing() -> None:
    client = FakeSerenDBClient(
        projects=[{"id": "prj_existing", "name": "pk-lead-intelligence"}],
        databases={"prj_existing": []},
        connection_uris={"prj_existing": "postgresql://example/empty"},
    )

    uri = p.bootstrap_serendb(
        project_name="pk-lead-intelligence",
        database_name="pk_lead_intelligence",
        client=client,
    )

    assert client.create_project_calls == []
    assert client.create_database_calls == [
        ("prj_existing", "pk_lead_intelligence"),
    ]
    assert uri == "postgresql://example/empty"


def test_does_not_re_create_database_when_only_project_was_missing() -> None:
    """`create_project` returns a project — bootstrap MUST not assume the
    new project has the target database already, but MUST also not
    list-databases on a freshly-created project before creating one.

    This guards against double-create races where a stale list-call
    silently swallows a create-database call.
    """

    client = FakeSerenDBClient(projects=[])

    p.bootstrap_serendb(
        project_name="pk-lead-intelligence",
        database_name="pk_lead_intelligence",
        client=client,
    )

    # Project was created. Database was created exactly once against
    # the new project. The database was not double-created.
    assert client.create_database_calls == [
        ("prj_pk-lead-intelligence", "pk_lead_intelligence"),
    ]


# --------------------------------------------------------------------- #
# Failure paths                                                         #
# --------------------------------------------------------------------- #


def test_raises_if_get_connection_uri_returns_empty_string() -> None:
    client = FakeSerenDBClient(
        projects=[{"id": "prj_x", "name": "pk-lead-intelligence"}],
        databases={"prj_x": [{"id": "db_x", "name": "pk_lead_intelligence"}]},
        connection_uris={"prj_x": ""},
    )

    with pytest.raises(RuntimeError, match="connection URI"):
        p.bootstrap_serendb(
            project_name="pk-lead-intelligence",
            database_name="pk_lead_intelligence",
            client=client,
        )


def test_project_name_match_is_exact_not_substring() -> None:
    """Two projects with similar names must not collide. Bootstrap must
    match the requested name exactly; substring matches silently send
    the skill at the wrong project — a P0 data-loss class of bug.
    """

    client = FakeSerenDBClient(
        projects=[
            {"id": "prj_other", "name": "pk-lead-intelligence-staging"},
            {"id": "prj_target", "name": "pk-lead-intelligence"},
        ],
        databases={
            "prj_other": [{"id": "db_other", "name": "pk_lead_intelligence"}],
            "prj_target": [{"id": "db_target", "name": "pk_lead_intelligence"}],
        },
        connection_uris={
            "prj_other": "postgresql://staging/should_not_be_returned",
            "prj_target": "postgresql://prod/expected",
        },
    )

    uri = p.bootstrap_serendb(
        project_name="pk-lead-intelligence",
        database_name="pk_lead_intelligence",
        client=client,
    )

    assert uri == "postgresql://prod/expected"
    assert client.create_project_calls == []


def test_database_name_match_is_exact_not_substring() -> None:
    """Same exact-match guard as the project test, but for databases."""

    client = FakeSerenDBClient(
        projects=[{"id": "prj_x", "name": "pk-lead-intelligence"}],
        databases={
            "prj_x": [
                {"id": "db_archive", "name": "pk_lead_intelligence_archive"},
            ],
        },
        connection_uris={"prj_x": "postgresql://example/pk"},
    )

    p.bootstrap_serendb(
        project_name="pk-lead-intelligence",
        database_name="pk_lead_intelligence",
        client=client,
    )

    # The archive database is NOT a match — bootstrap creates the real
    # one rather than silently returning the URI for the archive.
    assert client.create_database_calls == [
        ("prj_x", "pk_lead_intelligence"),
    ]


# --------------------------------------------------------------------- #
# Idempotency: a second call must be a no-op                            #
# --------------------------------------------------------------------- #


def test_second_call_is_no_op() -> None:
    """Bootstrap is meant to run on every invoke. A second call against
    an already-bootstrapped client must produce zero create calls and
    return the same URI.
    """

    client = FakeSerenDBClient()

    uri1 = p.bootstrap_serendb(
        project_name="pk-lead-intelligence",
        database_name="pk_lead_intelligence",
        client=client,
    )
    create_project_count = len(client.create_project_calls)
    create_database_count = len(client.create_database_calls)

    uri2 = p.bootstrap_serendb(
        project_name="pk-lead-intelligence",
        database_name="pk_lead_intelligence",
        client=client,
    )

    assert uri1 == uri2
    assert len(client.create_project_calls) == create_project_count
    assert len(client.create_database_calls) == create_database_count
