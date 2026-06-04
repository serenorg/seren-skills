from __future__ import annotations

from typing import Any


_DEFAULT_REGION = "aws-us-east-2"


class SerenDBManager:
    """Resolve-or-create the skill's SerenDB project + database in-flow.

    seren-db is a REST management API (projects -> branches -> databases);
    SQL executes via ``POST /query`` with
    ``{project_id, branch_id, database, query}``. The project and database
    are created on first run and reused thereafter, mirroring the sibling
    pk-lead-intelligence bootstrap (issue #867; same branch-scoped class as
    #855). Returns the ``(project_id, branch_id)`` the audit ledger needs.
    """

    PUBLISHER = "seren-db"

    def __init__(self, gateway: Any, *, region: str = _DEFAULT_REGION) -> None:
        self.gateway = gateway
        self.region = region

    def ensure_project_database(
        self, *, project_name: str, database_name: str
    ) -> tuple[str, str]:
        project = self._find_project(project_name)
        if project is None:
            project_id, branch_id = self._create_project(project_name)
            # A freshly-created project is empty — create the database
            # directly without a list round-trip (avoids a create/list race).
            self._create_database(project_id, branch_id, database_name)
        else:
            project_id, branch_id = project
            if not self._database_exists(project_id, branch_id, database_name):
                self._create_database(project_id, branch_id, database_name)
        return project_id, branch_id

    # ---- management calls --------------------------------------------- #

    def _find_project(self, name: str) -> tuple[str, str] | None:
        items = _as_items(self._get("/projects"), ("projects", "data", "items"))
        for item in items:
            if item.get("name") == name:
                return self._coords(item)
        return None

    def _create_project(self, name: str) -> tuple[str, str]:
        item = self._post("/projects", {"name": name, "region": self.region})
        return self._coords(item)

    def _database_exists(self, project_id: str, branch_id: str, name: str) -> bool:
        path = f"/projects/{project_id}/branches/{branch_id}/databases"
        items = _as_items(self._get(path), ("databases", "data", "items"))
        return any(item.get("name") == name for item in items)

    def _create_database(self, project_id: str, branch_id: str, name: str) -> None:
        path = f"/projects/{project_id}/branches/{branch_id}/databases"
        self._post(path, {"name": name})

    def _coords(self, project: dict) -> tuple[str, str]:
        project_id = project.get("id") or project.get("project_id")
        branch_id = project.get("default_branch_id")
        if not project_id:
            raise RuntimeError("seren-db project payload missing id")
        if not branch_id:
            # Cold payload without an inline default branch — fetch it.
            fetched = self._get(f"/projects/{project_id}")
            branch_id = fetched.get("default_branch_id") if isinstance(fetched, dict) else None
        if not branch_id:
            raise RuntimeError(
                f"seren-db: cannot resolve default branch for project {project_id!r}"
            )
        return str(project_id), str(branch_id)

    # ---- transport ---------------------------------------------------- #

    def _get(self, path: str) -> Any:
        return self.gateway.call_publisher(self.PUBLISHER, method="GET", path=path)

    def _post(self, path: str, body: dict) -> Any:
        return self.gateway.call_publisher(
            self.PUBLISHER, method="POST", path=path, body=body
        )


def _as_items(resp: Any, keys: tuple[str, ...]) -> list[dict]:
    if isinstance(resp, list):
        return [item for item in resp if isinstance(item, dict)]
    if isinstance(resp, dict):
        for key in keys:
            value = resp.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []
