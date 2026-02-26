"""SerenDB persistence for Kraken Money Mode Router via local seren-mcp."""

from __future__ import annotations

import json
import os
import select
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


class SerenMCPError(RuntimeError):
    """Raised when a local seren-mcp tool call fails."""


@dataclass
class DBTarget:
    project_id: str
    branch_id: str
    database: str
    endpoint_id: Optional[str] = None


class _SerenMCPClient:
    def __init__(self, api_key: Optional[str] = None, mcp_command: str = "seren-mcp", timeout_seconds: int = 30):
        self.api_key = api_key
        self.mcp_command = mcp_command
        self.timeout_seconds = timeout_seconds
        self._process: Optional[subprocess.Popen[str]] = None
        self._next_id = 1

    def start(self) -> None:
        if self._process is not None:
            return

        env = os.environ.copy()
        if self.api_key:
            env["API_KEY"] = self.api_key

        self._process = subprocess.Popen(
            [self.mcp_command, "start"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )

        if self._process.stdin is None or self._process.stdout is None:
            raise SerenMCPError("Failed to open stdio pipes for seren-mcp")

        init_response = self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "kraken-money-mode-router", "version": "1.0.0"},
            },
        )
        if "error" in init_response:
            raise SerenMCPError(init_response["error"].get("message", "seren-mcp initialize failed"))

        self._notify("notifications/initialized", {})

    def close(self) -> None:
        if self._process is None:
            return

        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

        self._process = None

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        response = self._request("tools/call", {"name": name, "arguments": arguments})
        if "error" in response:
            message = response["error"].get("message", "Unknown MCP tool error")
            raise SerenMCPError(f"{name} failed: {message}")

        result = response.get("result", {})
        if isinstance(result, dict) and result.get("isError"):
            payload = self._parse_tool_result(result)
            raise SerenMCPError(f"{name} returned isError: {payload}")
        return self._parse_tool_result(result)

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            message = self._read_message(timeout=0.5)
            if message is None:
                continue
            if message.get("id") == request_id:
                return message

        raise SerenMCPError(f"Timed out waiting for MCP response to {method}")

    def _send(self, message: Dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise SerenMCPError("seren-mcp process is not started")
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _read_message(self, timeout: float) -> Optional[Dict[str, Any]]:
        if self._process is None or self._process.stdout is None:
            raise SerenMCPError("seren-mcp process is not started")

        ready, _, _ = select.select([self._process.stdout], [], [], timeout)
        if not ready:
            if self._process.poll() is not None:
                raise SerenMCPError("seren-mcp exited unexpectedly")
            return None

        line = self._process.stdout.readline()
        if line == "":
            if self._process.poll() is not None:
                raise SerenMCPError("seren-mcp closed stdout unexpectedly")
            return None

        payload = line.strip()
        if not payload:
            return None

        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
            return None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _parse_tool_result(result: Any) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {"raw": result}

        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        if isinstance(structured, list):
            return {"data": structured}

        content = result.get("content")
        if isinstance(content, list):
            text_parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
            if len(text_parts) == 1:
                text = text_parts[0].strip()
                if text.startswith("{") or text.startswith("["):
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            return parsed
                        return {"data": parsed}
                    except json.JSONDecodeError:
                        return {"text": text_parts[0]}
                return {"text": text_parts[0]}
            if text_parts:
                return {"text": "\n".join(text_parts)}

        return result


class SerenDBStore:
    """Stores router sessions, answers, recommendations, and events in SerenDB via MCP."""

    DEFAULT_KRAKEN_PROJECT = "krakent"
    DEFAULT_KRAKEN_DATABASE = "krakent"

    def __init__(
        self,
        api_key: Optional[str] = None,
        project_name: Optional[str] = None,
        database_name: Optional[str] = None,
        branch_name: Optional[str] = None,
        project_region: str = "aws-us-east-1",
        auto_create: bool = True,
        mcp_command: str = "seren-mcp",
    ):
        self.project_name = project_name.strip() if project_name else None
        self.database_name = database_name.strip() if database_name else None
        self.branch_name = branch_name.strip() if branch_name else None
        self.project_region = project_region
        self.auto_create = auto_create
        self._target: Optional[DBTarget] = None
        self._mcp = _SerenMCPClient(api_key=api_key, mcp_command=mcp_command)
        self._mcp.start()

    def close(self) -> None:
        self._mcp.close()

    def ensure_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS kraken_skill_sessions (
            session_id UUID PRIMARY KEY,
            profile_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS kraken_skill_answers (
            id BIGSERIAL PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES kraken_skill_sessions(session_id) ON DELETE CASCADE,
            question_key TEXT NOT NULL,
            answer_value TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS kraken_skill_recommendations (
            id BIGSERIAL PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES kraken_skill_sessions(session_id) ON DELETE CASCADE,
            rank_index INTEGER NOT NULL,
            mode_id TEXT NOT NULL,
            score NUMERIC NOT NULL,
            label TEXT NOT NULL,
            summary TEXT NOT NULL,
            reasons JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS kraken_skill_actions (
            id BIGSERIAL PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES kraken_skill_sessions(session_id) ON DELETE CASCADE,
            mode_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            action_text TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS kraken_skill_events (
            id BIGSERIAL PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES kraken_skill_sessions(session_id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
        self._execute_sql(ddl)

    def create_session(self, session_id: str, profile_name: str) -> None:
        query = f"""
        INSERT INTO kraken_skill_sessions (session_id, profile_name)
        VALUES ({self._sql_text(session_id)}::uuid, {self._sql_text(profile_name)});
        """
        self._execute_sql(query)

    def save_answers(self, session_id: str, answers: Dict[str, str]) -> None:
        if not answers:
            return
        values = ", ".join(
            f"({self._sql_text(session_id)}::uuid, {self._sql_text(key)}, {self._sql_text(value)})"
            for key, value in answers.items()
        )
        query = f"""
        INSERT INTO kraken_skill_answers (session_id, question_key, answer_value)
        VALUES {values};
        """
        self._execute_sql(query)

    def save_recommendations(self, session_id: str, recommendations: List[Dict[str, Any]]) -> None:
        if not recommendations:
            return
        values: List[str] = []
        for idx, rec in enumerate(recommendations, start=1):
            score = float(rec.get("score", 0.0))
            values.append(
                (
                    f"({self._sql_text(session_id)}::uuid, {idx}, {self._sql_text(str(rec['mode_id']))}, "
                    f"{score}, {self._sql_text(str(rec['label']))}, {self._sql_text(str(rec['summary']))}, "
                    f"{self._sql_json(rec.get('reasons', []))})"
                )
            )
        query = f"""
        INSERT INTO kraken_skill_recommendations (
            session_id,
            rank_index,
            mode_id,
            score,
            label,
            summary,
            reasons
        )
        VALUES {", ".join(values)};
        """
        self._execute_sql(query)

    def save_actions(self, session_id: str, mode_id: str, actions: List[str]) -> None:
        if not actions:
            return
        values = ", ".join(
            f"({self._sql_text(session_id)}::uuid, {self._sql_text(mode_id)}, {idx}, {self._sql_text(text)})"
            for idx, text in enumerate(actions, start=1)
        )
        query = f"""
        INSERT INTO kraken_skill_actions (session_id, mode_id, step_index, action_text)
        VALUES {values};
        """
        self._execute_sql(query)

    def save_event(self, session_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        query = f"""
        INSERT INTO kraken_skill_events (session_id, event_type, payload)
        VALUES ({self._sql_text(session_id)}::uuid, {self._sql_text(event_type)}, {self._sql_json(payload)});
        """
        self._execute_sql(query)

    def _execute_sql(self, query: str) -> None:
        target = self._resolve_target()
        last_error: Optional[Exception] = None
        for _ in range(3):
            try:
                self._mcp.call_tool(
                    "run_sql",
                    {
                        "project_id": target.project_id,
                        "branch_id": target.branch_id,
                        "database": target.database,
                        "query": query,
                    },
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self._attempt_endpoint_recovery(target)
                time.sleep(2)
        raise SerenMCPError(f"run_sql failed after retries: {last_error}")

    def _resolve_target(self) -> DBTarget:
        if self._target is not None:
            return self._target

        all_dbs = self._mcp.call_tool("list_all_databases", {})
        candidates = all_dbs.get("databases", [])
        if not isinstance(candidates, list):
            candidates = []

        if self.database_name:
            self._target = self._find_target(
                candidates=candidates,
                project_name=self.project_name,
                database_name=self.database_name,
                branch_name=self.branch_name,
            )
        else:
            self._target = self._find_existing_kraken_target(candidates=candidates)

        if self._target is not None:
            self._attempt_endpoint_recovery(self._target)
            return self._target

        if not self.auto_create:
            raise SerenMCPError(
                "Target SerenDB database not found via seren-mcp. "
                "Set SERENDB_DATABASE/SERENDB_PROJECT_NAME or enable SERENDB_AUTO_CREATE=true."
            )

        project_name = self.project_name or self.DEFAULT_KRAKEN_PROJECT
        database_name = self.database_name or self.DEFAULT_KRAKEN_DATABASE
        self._target = self._provision_target(project_name=project_name, database_name=database_name)
        self._attempt_endpoint_recovery(self._target)
        return self._target

    def _provision_target(self, project_name: str, database_name: str) -> DBTarget:
        projects_payload = self._mcp.call_tool("list_projects", {})
        projects = projects_payload.get("data", [])
        if not isinstance(projects, list):
            projects = []

        project = None
        for item in projects:
            if isinstance(item, dict) and item.get("name") == project_name:
                project = item
                break

        if project is None:
            created = self._mcp.call_tool(
                "create_project",
                {"name": project_name, "region": self.project_region},
            )
            project = created.get("data", created)

        project_id = str(project["id"])
        branch_id = str(project.get("default_branch_id") or "")
        if not branch_id:
            branches_payload = self._mcp.call_tool("list_branches", {"project_id": project_id})
            branches = branches_payload.get("data", [])
            if not isinstance(branches, list) or not branches:
                raise SerenMCPError("No branches found after project provisioning")
            default_branch = next((b for b in branches if isinstance(b, dict) and b.get("is_default")), branches[0])
            branch_id = str(default_branch["id"])

        dbs_payload = self._mcp.call_tool(
            "list_databases",
            {"project_id": project_id, "branch_id": branch_id},
        )
        dbs = dbs_payload.get("databases", dbs_payload.get("data", []))
        if not isinstance(dbs, list):
            dbs = []
        has_database = any(isinstance(db, dict) and db.get("name") == database_name for db in dbs)
        if not has_database:
            try:
                self._mcp.call_tool(
                    "create_database",
                    {
                        "project_id": project_id,
                        "branch_id": branch_id,
                        "name": database_name,
                    },
                )
            except Exception:
                pass

            dbs_payload = self._mcp.call_tool(
                "list_databases",
                {"project_id": project_id, "branch_id": branch_id},
            )
            dbs = dbs_payload.get("databases", dbs_payload.get("data", []))
            if not isinstance(dbs, list):
                dbs = []
            has_database = any(isinstance(db, dict) and db.get("name") == database_name for db in dbs)
            if not has_database:
                raise SerenMCPError(
                    f"Database '{database_name}' not found after provisioning for project '{project_name}'"
                )

        return DBTarget(project_id=project_id, branch_id=branch_id, database=database_name)

    def _find_target(
        self,
        candidates: List[Any],
        project_name: Optional[str],
        database_name: str,
        branch_name: Optional[str],
    ) -> Optional[DBTarget]:
        for db in candidates:
            if not isinstance(db, dict):
                continue
            db_project_name = self._normalize_name(db.get("project"))
            db_database_name = self._normalize_name(db.get("database") or db.get("name"))
            db_branch_name = self._normalize_name(db.get("branch"))
            if project_name and db_project_name != self._normalize_name(project_name):
                continue
            if db_database_name != self._normalize_name(database_name):
                continue
            if branch_name and db_branch_name != self._normalize_name(branch_name):
                continue
            project_id = db.get("project_id")
            branch_id = db.get("branch_id")
            if not project_id or not branch_id:
                continue
            return DBTarget(
                project_id=str(project_id),
                branch_id=str(branch_id),
                database=str(db.get("database") or db.get("name") or database_name),
            )
        return None

    def _find_existing_kraken_target(self, candidates: List[Any]) -> Optional[DBTarget]:
        scored: List[Tuple[int, DBTarget]] = []
        requested_project_name = self._normalize_name(self.project_name)
        requested_branch_name = self._normalize_name(self.branch_name)

        for db in candidates:
            if not isinstance(db, dict):
                continue
            project_id = db.get("project_id")
            branch_id = db.get("branch_id")
            if not project_id or not branch_id:
                continue

            project_name = self._normalize_name(db.get("project"))
            database_name = self._normalize_name(db.get("database") or db.get("name"))
            branch_name = self._normalize_name(db.get("branch"))
            if requested_project_name and project_name != requested_project_name:
                continue
            if requested_branch_name and branch_name != requested_branch_name:
                continue

            score = 0
            if database_name == "kraken":
                score += 200
            if project_name == "kraken":
                score += 180
            if database_name == "krakent":
                score += 170
            if project_name == "krakent":
                score += 150
            if "kraken" in database_name:
                score += 120
            if "kraken" in project_name:
                score += 100

            if score == 0:
                continue

            scored.append(
                (
                    score,
                    DBTarget(
                        project_id=str(project_id),
                        branch_id=str(branch_id),
                        database=str(db.get("database") or db.get("name") or "kraken"),
                    ),
                )
            )

        if not scored:
            return None

        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def _attempt_endpoint_recovery(self, target: DBTarget) -> None:
        try:
            endpoints_payload = self._mcp.call_tool(
                "list_endpoints",
                {"project_id": target.project_id, "branch_id": target.branch_id},
            )
            endpoints = endpoints_payload.get("data", [])
            if not isinstance(endpoints, list) or not endpoints:
                return
            endpoint = endpoints[0]
            if not isinstance(endpoint, dict):
                return
            endpoint_id = str(endpoint["id"])
            target.endpoint_id = endpoint_id
            status = str(endpoint.get("status", ""))
            if status == "suspended":
                try:
                    self._mcp.call_tool(
                        "start_endpoint",
                        {
                            "project_id": target.project_id,
                            "branch_id": target.branch_id,
                            "endpoint_id": endpoint_id,
                        },
                    )
                except Exception:
                    self._mcp.call_tool(
                        "restart_endpoint",
                        {
                            "project_id": target.project_id,
                            "endpoint_id": endpoint_id,
                        },
                    )
            elif status not in {"active", "running"}:
                self._mcp.call_tool(
                    "restart_endpoint",
                    {
                        "project_id": target.project_id,
                        "endpoint_id": endpoint_id,
                    },
                )
        except Exception:
            return

    @staticmethod
    def _sql_text(value: Any) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def _sql_json(value: Any) -> str:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).replace("'", "''")
        return f"'{encoded}'::jsonb"

    @staticmethod
    def _normalize_name(value: Any) -> str:
        return str(value or "").strip().lower()
