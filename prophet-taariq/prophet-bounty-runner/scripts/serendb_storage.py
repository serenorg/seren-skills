"""SerenDB-backed persistence for prophet-bounty-runner (issue #474).

Replaces `agent._InMemoryStorage` for production runs so the operator's
reconciler, the daily submission folding, and the per-user attribution
machinery (plan §22 #12) actually have durable state to read.

Contract:

  * Same `insert(table, row)` interface the in-memory stand-in exposes,
    plus a `markets_created` read property — the only attribute
    `agent._cmd_run` and `agent._cmd_status` access via `getattr`. No
    other agent.py changes are required to swap stores.
  * Lazy schema bootstrap on first insert. Templated `{{schema_name}}`
    in `serendb_schema.sql` is rendered to the runtime schema name. A
    module-level cache prevents re-running DDL on a second instance
    targeting the same `(gateway, schema_name)` tuple.
  * Cold-start retry. SerenDB scales to zero; the first /query after
    idle returns 503 / connection failures until the database wakes
    (~10–30s). The store retries past a small warmup window before
    failing closed, so the first cron tick of an idle period does not
    silently drop its writes.

Routes through the same `seren-db` publisher pattern that
`polymarket/bot/scripts/serendb_storage.py` uses against the live
gateway, called via the existing prophet-bounty-runner HttpGateway
seam so every test path can substitute a fake.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_PROJECT_NAME = "prophet"
DEFAULT_DATABASE_NAME = "prophet_bounty_runner"
DEFAULT_SCHEMA_NAME = "prophet_bounty_runner"
DEFAULT_REGION = "aws-us-east-2"
SCHEMA_SQL_PATH = Path(__file__).resolve().parent.parent / "serendb_schema.sql"

_BOOTSTRAPPED: set[tuple[int, str]] = set()


def reset_bootstrap_cache() -> None:
    """Clear the per-process bootstrap memo. Tests use this so two
    `SerenDBStorage` instances against the same gateway act independent
    until the second one is meant to short-circuit."""
    _BOOTSTRAPPED.clear()


# Sentinels the warmup retry recognizes as "database is waking".
_WARMUP_MARKERS = ("503", "service unavailable", "warming up", "timed out", "timeout")


class SerenDBStorage:
    """Same shape as `agent._InMemoryStorage`, but writes through the
    `seren-db` publisher.

    The class generates one `run_id` per instance and stamps it on every
    insert that needs one (plan §17.1 schema). The first non-`runs`
    insert seeds a placeholder `runs` row so the FK constraint on
    `markets_created.run_id` does not fail. The final `runs` insert
    upserts on top of that placeholder.
    """

    def __init__(
        self,
        *,
        gateway: Any,
        schema_name: str = DEFAULT_SCHEMA_NAME,
        project_name: str = DEFAULT_PROJECT_NAME,
        warmup_max_attempts: int = 6,
        warmup_initial_delay_seconds: float = 1.0,
        warmup_max_delay_seconds: float = 8.0,
    ) -> None:
        self.gateway = gateway
        self.schema_name = schema_name
        self.project_name = project_name
        self.warmup_max_attempts = max(1, int(warmup_max_attempts))
        self.warmup_initial_delay_seconds = max(0.0, float(warmup_initial_delay_seconds))
        self.warmup_max_delay_seconds = max(
            self.warmup_initial_delay_seconds, float(warmup_max_delay_seconds)
        )
        self.run_id = str(uuid.uuid4())
        self.user_id = (os.getenv("SEREN_USER_ID") or "").strip() or "agent"
        self._project_id: str | None = None
        self._branch_id: str | None = None
        self._runs_seeded = False

    # ------------------------------------------------------------------
    # Public surface

    def insert(self, table: str, row: dict) -> None:
        """Append a row to the named table. Mirrors the in-memory shape
        so `agent._cmd_run` does not need to know which store it has."""
        if not isinstance(row, dict):
            raise TypeError(f"row must be dict, got {type(row).__name__}")
        self.bootstrap()
        if table == "runs":
            self._upsert_runs(row)
            return
        if not self._runs_seeded:
            self._seed_running_runs_row(row)
        enriched = self._enrich(table, row)
        self._exec_insert(table, enriched)

    @property
    def markets_created(self) -> list[dict]:
        """Read-through view used by `_load_prior_markets` and
        `_count_local_markets`. Driven by SELECT so a fresh process
        sees rows from earlier ticks — the bug fixed in issue #474."""
        self.bootstrap()
        return self._select_all("markets_created")

    @property
    def runs(self) -> list[dict]:
        return self._select_all("runs") if self._project_id else []

    @property
    def submissions(self) -> list[dict]:
        return self._select_all("submissions") if self._project_id else []

    @property
    def events(self) -> list[dict]:
        return self._select_all("events") if self._project_id else []

    @property
    def participant_identity(self) -> list[dict]:
        return self._select_all("participant_identity") if self._project_id else []

    # ------------------------------------------------------------------
    # Bootstrap

    def bootstrap(self) -> None:
        key = (id(self.gateway), self.schema_name)
        if key in _BOOTSTRAPPED:
            # Still need to know our project/branch ids on this instance.
            self._discover_project_and_branch()
            return
        self._discover_project_and_branch()
        self._apply_schema()
        _BOOTSTRAPPED.add(key)

    def _discover_project_and_branch(self) -> None:
        if self._project_id and self._branch_id:
            return
        projects = self._unwrap(self._call("GET", "/projects"))
        if isinstance(projects, dict):
            projects = projects.get("projects") or projects.get("data") or []
        project = None
        for p in projects or []:
            if isinstance(p, dict) and p.get("name") == self.project_name:
                project = p
                break
        if not project:
            created = self._unwrap(
                self._call(
                    "POST",
                    "/projects",
                    body={"name": self.project_name, "region": DEFAULT_REGION},
                )
            )
            project = created if isinstance(created, dict) else {}
        self._project_id = (project or {}).get("id") or ""
        if not self._project_id:
            raise RuntimeError("seren-db: could not resolve project id")
        branches = self._unwrap(
            self._call("GET", f"/projects/{self._project_id}/branches")
        )
        branch = None
        for b in branches or []:
            if not isinstance(b, dict):
                continue
            if b.get("default") or b.get("name") in ("production", "main"):
                branch = b
                break
        if not branch and branches:
            branch = branches[0] if isinstance(branches[0], dict) else None
        if not branch:
            raise RuntimeError("seren-db: no usable branch on project")
        self._branch_id = branch.get("id") or ""
        if not self._branch_id:
            raise RuntimeError("seren-db: branch id missing")

    def _apply_schema(self) -> None:
        try:
            sql_template = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        sql = sql_template.replace("{{schema_name}}", self.schema_name)
        # The query API takes one statement at a time; split on ';' and
        # apply non-blank statements individually so a partial-apply
        # error surfaces a real diagnostic rather than a cryptic 400.
        # Strip leading comment lines so a chunk that is only an SQL
        # comment block doesn't get sent as a no-op query.
        for stmt in sql.split(";"):
            cleaned = "\n".join(
                line for line in stmt.splitlines()
                if line.strip() and not line.lstrip().startswith("--")
            ).strip()
            if not cleaned:
                continue
            self._exec_query(cleaned)

    # ------------------------------------------------------------------
    # Inserts

    def _enrich(self, table: str, row: dict) -> dict:
        out = dict(row)
        if table in ("markets_created", "submissions", "events"):
            out.setdefault("run_id", self.run_id)
        if table == "participant_identity":
            out.setdefault("seren_user_id", self.user_id)
        if table == "submissions":
            out.setdefault("submission_id", str(uuid.uuid4()))
            out.setdefault("status", "submitted")
        if table == "events":
            # event_id is BIGSERIAL — let the database assign it.
            out.pop("event_id", None)
            out.setdefault("event_type", "unknown")
        return out

    def _seed_running_runs_row(self, peek_row: dict) -> None:
        """Insert a placeholder `runs` row so child tables can FK to it."""
        bounty_id = peek_row.get("bounty_id") or "unknown"
        seed = {
            "run_id": self.run_id,
            "bounty_id": bounty_id,
            "user_id": self.user_id,
            "command": "run",
            "dry_run": False,
            "status": "succeeded",  # provisional; final upsert overwrites.
        }
        self._exec_insert("runs", seed)
        self._runs_seeded = True

    def _upsert_runs(self, row: dict) -> None:
        full = {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "command": row.get("command") or "run",
            "dry_run": bool(row.get("dry_run", False)),
            "status": row.get("status") or "succeeded",
            "bounty_id": row.get("bounty_id") or "unknown",
        }
        if self._runs_seeded:
            sets = ", ".join(
                f"{k} = {self._format_value(v)}"
                for k, v in full.items()
                if k != "run_id"
            )
            sql = (
                f"UPDATE {self.schema_name}.runs SET {sets} "
                f"WHERE run_id = {self._format_value(full['run_id'])}"
            )
            self._exec_query(sql)
        else:
            self._exec_insert("runs", full)
            self._runs_seeded = True

    def _exec_insert(self, table: str, row: dict) -> None:
        # Filter to schema columns and drop unsupported keys (e.g. the
        # legacy `market_count` runs column the in-memory stand-in
        # accepted but the §17 schema does not define).
        allowed = _SCHEMA_COLUMNS.get(table, set(row.keys()))
        items = [(k, v) for k, v in row.items() if k in allowed]
        if not items:
            return
        cols = ", ".join(k for k, _ in items)
        vals = ", ".join(self._format_value(v) for _, v in items)
        sql = f"INSERT INTO {self.schema_name}.{table} ({cols}) VALUES ({vals})"
        self._exec_query(sql)

    # ------------------------------------------------------------------
    # Reads

    def _select_all(self, table: str) -> list[dict]:
        if not self._project_id or not self._branch_id:
            return []
        sql = f"SELECT * FROM {self.schema_name}.{table}"
        result = self._exec_query(sql)
        rows = self._unwrap(result)
        if isinstance(rows, dict):
            rows = rows.get("rows") or rows.get("data") or []
        return [r for r in (rows or []) if isinstance(r, dict)]

    # ------------------------------------------------------------------
    # Gateway plumbing

    def _exec_query(self, query: str) -> Any:
        if not self._project_id or not self._branch_id:
            self._discover_project_and_branch()
        body = {
            "project_id": self._project_id,
            "branch_id": self._branch_id,
            "query": query,
        }
        return self._call_with_warmup("POST", "/query", body=body)

    def _call_with_warmup(self, method: str, path: str, *, body: Any = None) -> Any:
        delay = self.warmup_initial_delay_seconds
        last: Exception | None = None
        for attempt in range(self.warmup_max_attempts):
            try:
                return self._call(method, path, body=body)
            except Exception as exc:
                if not _is_warmup_error(exc):
                    raise
                last = exc
                if attempt + 1 >= self.warmup_max_attempts:
                    break
                if delay > 0:
                    time.sleep(min(delay, self.warmup_max_delay_seconds))
                    delay = min(delay * 2 if delay else 1.0, self.warmup_max_delay_seconds)
        raise RuntimeError(f"seren-db warmup exhausted after {self.warmup_max_attempts} attempts: {last}")

    def _call(self, method: str, path: str, *, body: Any = None) -> Any:
        return self.gateway.call("seren-db", method, path, body=body)

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        if payload is None:
            return None
        if isinstance(payload, dict):
            if "data" in payload:
                return payload["data"]
            if "body" in payload and isinstance(payload["body"], (dict, list)):
                return payload["body"]
        return payload

    @staticmethod
    def _format_value(v: Any) -> str:
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return str(v)
        s = str(v).replace("'", "''")
        return f"'{s}'"


_SCHEMA_COLUMNS: dict[str, set[str]] = {
    "runs": {
        "run_id",
        "bounty_id",
        "user_id",
        "command",
        "dry_run",
        "status",
        "created_at",
        "finished_at",
    },
    "participant_identity": {
        "bounty_id",
        "seren_user_id",
        "prophet_viewer_id",
        "prophet_email",
        "captured_at",
    },
    "markets_created": {
        "prophet_market_id",
        "run_id",
        "prophet_market_url",
        "polymarket_source_url",
        "resolves_at",
        "prophet_viewer_id",
        "bounty_id",
        "created_at",
    },
    "submissions": {
        "submission_id",
        "bounty_id",
        "run_id",
        "status",
        "payload",
        "created_at",
    },
    "events": {
        "run_id",
        "event_type",
        "payload",
        "created_at",
    },
}


def _is_warmup_error(exc: BaseException) -> bool:
    """Classify a gateway exception as a scale-to-zero cold-start signal."""
    msg = str(exc).lower()
    if any(marker in msg for marker in _WARMUP_MARKERS):
        return True
    name = type(exc).__name__.lower()
    if "503" in name or "timeout" in name:
        return True
    return False
