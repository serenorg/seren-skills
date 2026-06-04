from __future__ import annotations

import re

from scripts.audit import SerenDBAuditLedger
from scripts.serendb import SerenDBManager


class FakeSerenDBGateway:
    """Records seren-db management + /query calls against a fake store."""

    _DB_PATH = re.compile(r"^/projects/([^/]+)/branches/([^/]+)/databases$")

    def __init__(self, projects=None) -> None:
        self._projects = projects or []
        self.created_projects: list[str] = []
        self.created_databases: list[tuple[str, str]] = []
        self.queries: list[dict] = []
        self.sql_rows: list[list] = []
        self._n = 100

    def call_publisher(self, publisher, *, method="GET", path="/", body=None, headers=None, response_format="json"):
        assert publisher == "seren-db"
        if method == "GET" and path == "/projects":
            return {"data": [
                {"id": p["id"], "name": p["name"], "default_branch_id": p["default_branch_id"]}
                for p in self._projects
            ]}
        if method == "POST" and path == "/projects":
            pid, bid = f"proj-{self._n}", f"branch-{self._n}"
            self._n += 1
            self._projects.append({"id": pid, "name": body["name"], "default_branch_id": bid, "databases": set()})
            self.created_projects.append(body["name"])
            return {"id": pid, "name": body["name"], "default_branch_id": bid}
        m = self._DB_PATH.match(path)
        if m and method == "GET":
            return {"data": [{"name": n} for n in sorted(self._project(m.group(1))["databases"])]}
        if m and method == "POST":
            self._project(m.group(1))["databases"].add(body["name"])
            self.created_databases.append((m.group(1), body["name"]))
            return {"name": body["name"]}
        if method == "POST" and path == "/query":
            self.queries.append(body)
            return {"columns": ["c"], "rows": list(self.sql_rows), "row_count": len(self.sql_rows)}
        raise AssertionError(f"unexpected {method} {path}")

    def _project(self, pid):
        for p in self._projects:
            if p["id"] == pid:
                return p
        raise AssertionError(f"no project {pid}")


def _existing():
    return [{
        "id": "proj-1", "name": "glide-affinity-proposals",
        "default_branch_id": "branch-1", "databases": {"glide_affinity_proposals"},
    }]


def test_bootstrap_creates_project_and_database_when_absent():
    gw = FakeSerenDBGateway(projects=[])
    pid, bid = SerenDBManager(gw).ensure_project_database(
        project_name="glide-affinity-proposals",
        database_name="glide_affinity_proposals",
    )
    assert gw.created_projects == ["glide-affinity-proposals"]
    assert gw.created_databases == [(pid, "glide_affinity_proposals")]
    assert pid and bid


def test_bootstrap_is_idempotent_when_project_and_database_exist():
    gw = FakeSerenDBGateway(projects=_existing())
    pid, bid = SerenDBManager(gw).ensure_project_database(
        project_name="glide-affinity-proposals",
        database_name="glide_affinity_proposals",
    )
    assert (pid, bid) == ("proj-1", "branch-1")
    assert gw.created_projects == []
    assert gw.created_databases == []


def test_audit_ledger_runs_branch_scoped_sql_with_coordinates():
    gw = FakeSerenDBGateway(projects=_existing())
    ledger = SerenDBAuditLedger(
        gw, project_id="proj-1", branch_id="branch-1", database="glide_affinity_proposals"
    )
    ledger.ensure_schema()
    ledger.record_proposal(
        prospect_id="p1", mode="dry-run", artifact_name="a.pdf",
        request_key="p1:dry-run:2026-06-04",
    )
    assert gw.queries, "no SQL issued"
    for q in gw.queries:
        assert q["project_id"] == "proj-1"
        assert q["branch_id"] == "branch-1"
        assert q["database"] == "glide_affinity_proposals"
    assert any("INSERT INTO glide_affinity_proposals" in q["query"] for q in gw.queries)


def test_proposal_exists_parses_rows_not_envelope():
    gw = FakeSerenDBGateway(projects=_existing())
    ledger = SerenDBAuditLedger(
        gw, project_id="proj-1", branch_id="branch-1", database="glide_affinity_proposals"
    )
    gw.sql_rows = []
    assert ledger.proposal_exists("p1", "dry-run") is False
    gw.sql_rows = [[1]]
    assert ledger.proposal_exists("p1", "dry-run") is True
