from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class AuditLedger:
    def proposal_exists(self, prospect_id: str, mode: str) -> bool:
        raise NotImplementedError

    def record_proposal(
        self,
        *,
        prospect_id: str,
        mode: str,
        artifact_name: str,
        request_key: str,
    ) -> None:
        raise NotImplementedError


@dataclass
class InMemoryAuditLedger(AuditLedger):
    proposals: set[tuple[str, str]] = field(default_factory=set)
    proposal_records: list[dict[str, str]] = field(default_factory=list)
    emails: list[dict[str, str]] = field(default_factory=list)

    def proposal_exists(self, prospect_id: str, mode: str) -> bool:
        return (prospect_id, mode) in self.proposals

    def record_proposal(
        self,
        *,
        prospect_id: str,
        mode: str,
        artifact_name: str,
        request_key: str,
    ) -> None:
        self.proposals.add((prospect_id, mode))
        self.proposal_records.append(
            {
                "prospect_id": prospect_id,
                "mode": mode,
                "artifact_name": artifact_name,
                "request_key": request_key,
            }
        )

    def record_email(self, *, prospect_id: str, mode: str, message_id: str) -> None:
        self.emails.append(
            {"prospect_id": prospect_id, "mode": mode, "message_id": message_id}
        )


class SerenDBAuditLedger(AuditLedger):
    """Audit ledger backed by the seren-db ``POST /query`` route.

    SQL is branch-scoped: every statement carries
    ``{project_id, branch_id, database, query}`` (issue #867). The
    project/branch/database are resolved in-flow by ``SerenDBManager``
    before the ledger is constructed.
    """

    def __init__(
        self,
        gateway: Any,
        *,
        project_id: str,
        branch_id: str,
        database: str,
    ) -> None:
        self.gateway = gateway
        self.project_id = project_id
        self.branch_id = branch_id
        self.database = database

    def _run_sql(self, query: str) -> Any:
        return self.gateway.call_publisher(
            "seren-db",
            method="POST",
            path="/query",
            body={
                "project_id": self.project_id,
                "branch_id": self.branch_id,
                "database": self.database,
                "query": query,
            },
        )

    def ensure_schema(self) -> None:
        # The /query route runs one statement per call — issue each
        # CREATE TABLE separately rather than as a semicolon batch.
        self._run_sql(
            "CREATE TABLE IF NOT EXISTS glide_affinity_proposals ("
            "id BIGSERIAL PRIMARY KEY, prospect_id TEXT NOT NULL, mode TEXT NOT NULL, "
            "artifact_name TEXT NOT NULL, request_key TEXT NOT NULL UNIQUE, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        self._run_sql(
            "CREATE TABLE IF NOT EXISTS glide_affinity_emails ("
            "id BIGSERIAL PRIMARY KEY, prospect_id TEXT NOT NULL, mode TEXT NOT NULL, "
            "message_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )

    def proposal_exists(self, prospect_id: str, mode: str) -> bool:
        result = self._run_sql(
            "SELECT 1 FROM glide_affinity_proposals "
            f"WHERE prospect_id = '{_sql(prospect_id)}' AND mode = '{_sql(mode)}' "
            "LIMIT 1"
        )
        return bool(_rows(result))

    def record_proposal(
        self,
        *,
        prospect_id: str,
        mode: str,
        artifact_name: str,
        request_key: str,
    ) -> None:
        self._run_sql(
            "INSERT INTO glide_affinity_proposals "
            "(prospect_id, mode, artifact_name, request_key, created_at) VALUES "
            f"('{_sql(prospect_id)}', '{_sql(mode)}', '{_sql(artifact_name)}', "
            f"'{_sql(request_key)}', '{datetime.now(timezone.utc).isoformat()}') "
            "ON CONFLICT (request_key) DO NOTHING"
        )

    def record_email(self, *, prospect_id: str, mode: str, message_id: str) -> None:
        self._run_sql(
            "INSERT INTO glide_affinity_emails "
            "(prospect_id, mode, message_id, created_at) VALUES "
            f"('{_sql(prospect_id)}', '{_sql(mode)}', '{_sql(message_id)}', "
            f"'{datetime.now(timezone.utc).isoformat()}')"
        )


def _sql(value: str) -> str:
    return value.replace("'", "''")


def _rows(result: Any) -> list:
    """Extract the row list from a seren-db /query response.

    The route returns ``{"columns": [...], "rows": [...], "row_count": N}``;
    a bare list is tolerated for forward-compat.
    """

    if isinstance(result, dict):
        rows = result.get("rows")
        return rows if isinstance(rows, list) else []
    if isinstance(result, list):
        return result
    return []
