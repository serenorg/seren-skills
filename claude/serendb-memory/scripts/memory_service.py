#!/usr/bin/env python3
"""Claude Code memory sync helpers for the claude/serendb-memory skill."""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

import requests
from cryptography.fernet import Fernet

try:  # pragma: no cover - exercised through fallback behavior
    import keyring
    from keyring.errors import KeyringError, NoKeyringError
except Exception:  # pragma: no cover - import failure fallback
    keyring = None

    class KeyringError(Exception):
        pass

    class NoKeyringError(KeyringError):
        pass


SERVICE_NAME = "claude-serendb-memory"
MEMORY_INDEX_FILENAME = "MEMORY.md"
DEFAULT_MEMORY_TYPE = "claude_preference"
AUDIT_MEMORY_TYPE = "claude_memory_audit"
PROJECT_NAMESPACE = uuid.UUID("0ad9da16-8123-4d8c-aa39-b09b6f29bda1")


class MemorySyncError(RuntimeError):
    """Raised when the memory skill cannot complete a requested action."""


@dataclass
class MemoryFrontmatter:
    name: str | None = None
    description: str | None = None
    memory_type: str | None = None


@dataclass
class ParsedMemoryFile:
    frontmatter: MemoryFrontmatter
    body: str


@dataclass
class QueuePayload:
    encoded_project: str
    project_id: str
    source_file: str
    content_hash: str
    memory_type: str
    raw_content: str
    created_at: str
    reason: str


@dataclass
class ServiceConfig:
    memory_root: Path
    state_dir: Path
    api_base_url: str = "https://api.serendb.com"
    memory_base_url: str = "https://memory.serendb.com"
    poll_interval_seconds: int = 3
    dry_run: bool = False
    install_service_on_install: bool = True
    start_after_install: bool = True
    auto_register_key: bool = True
    timeout_seconds: int = 5
    service_name: str = SERVICE_NAME


class CloudClient(Protocol):
    def remember(self, content: str, memory_type: str, project_id: uuid.UUID) -> str:
        ...

    def pull_memories(self, project_id: uuid.UUID, *, limit: int = 200) -> list[dict[str, Any]]:
        ...


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def expand_path(value: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(value))).resolve(strict=False)


def desktop_block_reason(env: dict[str, str] | None = None) -> str | None:
    current = env or os.environ
    if current.get("SEREN_HOST") == "seren-desktop":
        return "SEREN_HOST=seren-desktop"
    if current.get("SEREN_DESKTOP") == "1":
        return "SEREN_DESKTOP=1"
    if current.get("SEREN_MCP_COMMAND"):
        return "SEREN_MCP_COMMAND is present"
    return None


def should_intercept_path(path: Path) -> bool:
    if path.suffix.lower() != ".md":
        return False
    if path.name.upper() == MEMORY_INDEX_FILENAME:
        return False
    return path.parent.name == "memory"


def parse_memory_file(contents: str) -> ParsedMemoryFile:
    normalized = contents.replace("\r\n", "\n").lstrip("\n")
    if not normalized.startswith("---\n"):
        return ParsedMemoryFile(frontmatter=MemoryFrontmatter(), body=normalized.strip())

    end_marker = "\n---"
    after_open = normalized[4:]
    end_idx = after_open.find(end_marker)
    if end_idx < 0:
        return ParsedMemoryFile(frontmatter=MemoryFrontmatter(), body=normalized.strip())

    frontmatter_text = after_open[:end_idx]
    body = after_open[end_idx + len(end_marker) :].lstrip("\n").strip()
    data: dict[str, str] = {}
    for raw_line in frontmatter_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            data[key.strip().lower()] = value
    return ParsedMemoryFile(
        frontmatter=MemoryFrontmatter(
            name=data.get("name"),
            description=data.get("description"),
            memory_type=data.get("type"),
        ),
        body=body,
    )


def encoded_project_from_path(path: Path) -> str:
    return path.parent.parent.name


def project_uuid(encoded_project: str) -> uuid.UUID:
    return uuid.uuid5(PROJECT_NAMESPACE, encoded_project)


def content_hash(raw_content: str) -> str:
    return sha256(raw_content.encode("utf-8")).hexdigest()


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


class CredentialStore:
    def __init__(self, state_dir: Path, *, service_name: str = SERVICE_NAME) -> None:
        self.state_dir = state_dir
        self.service_name = service_name
        self.api_key_fallback_path = self.state_dir / "seren_api_key"
        self.queue_key_fallback_path = self.state_dir / "queue_key"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _read_secret_file(self, path: Path) -> str | None:
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None

    def _write_secret_file(self, path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value.strip() + "\n", encoding="utf-8")
        os.chmod(path, 0o600)

    def read_api_key(self) -> tuple[str | None, str | None]:
        env_key = (os.getenv("SEREN_API_KEY") or "").strip()
        if env_key:
            return env_key, "env"
        if keyring is not None:
            try:
                stored = keyring.get_password(self.service_name, "SEREN_API_KEY")
                if stored:
                    return stored.strip(), "keyring"
            except (KeyringError, NoKeyringError, RuntimeError):
                pass
        file_key = self._read_secret_file(self.api_key_fallback_path)
        if file_key:
            return file_key, "file"
        return None, None

    def store_api_key(self, value: str) -> str:
        if keyring is not None:
            try:
                keyring.set_password(self.service_name, "SEREN_API_KEY", value)
                return "keyring"
            except (KeyringError, NoKeyringError, RuntimeError):
                pass
        self._write_secret_file(self.api_key_fallback_path, value)
        return "file"

    def load_queue_cipher(self) -> Fernet:
        secret = None
        if keyring is not None:
            try:
                secret = keyring.get_password(self.service_name, "QUEUE_ENCRYPTION_KEY")
            except (KeyringError, NoKeyringError, RuntimeError):
                secret = None
        if not secret:
            secret = self._read_secret_file(self.queue_key_fallback_path)
        if not secret:
            secret = Fernet.generate_key().decode("ascii")
            if keyring is not None:
                try:
                    keyring.set_password(self.service_name, "QUEUE_ENCRYPTION_KEY", secret)
                except (KeyringError, NoKeyringError, RuntimeError):
                    self._write_secret_file(self.queue_key_fallback_path, secret)
            else:
                self._write_secret_file(self.queue_key_fallback_path, secret)
        return Fernet(secret.encode("ascii"))


class SerenCloudClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_base_url: str,
        memory_base_url: str,
        timeout_seconds: int = 5,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip("/")
        self.memory_base_url = memory_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def validate_api_key(self) -> bool:
        try:
            response = self.session.get(
                f"{self.api_base_url}/auth/me",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException:
            return bool(self.api_key)
        return response.status_code < 400

    def create_api_key(self, *, name: str = SERVICE_NAME) -> str:
        response = self.session.post(
            f"{self.api_base_url}/auth/agent",
            json={"name": name},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise MemorySyncError(
                f"Seren API key registration failed: status={response.status_code} "
                f"body={response.text[:200]}"
            )
        body = response.json()
        candidates = [
            body.get("api_key"),
            body.get("key"),
            body.get("token"),
            body.get("value"),
        ]
        data = body.get("data")
        if isinstance(data, dict):
            agent = data.get("agent")
            if isinstance(agent, dict):
                candidates.extend(
                    [
                        agent.get("api_key"),
                        agent.get("key"),
                        agent.get("token"),
                        agent.get("value"),
                    ]
                )
            candidates.extend(
                [
                    data.get("api_key"),
                    data.get("key"),
                    data.get("token"),
                    data.get("value"),
                ]
            )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        raise MemorySyncError("Could not parse SEREN_API_KEY from /auth/agent response")

    def _call_mcp_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        response = self.session.post(
            f"{self.memory_base_url}/mcp",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise MemorySyncError(
                f"MCP tool {tool_name} failed: status={response.status_code} "
                f"body={response.text[:200]}"
            )
        body_text = response.text
        content_type = response.headers.get("content-type", "")
        json_text = (
            self._extract_sse_json(body_text)
            if "text/event-stream" in content_type
            else body_text
        )
        body = json.loads(json_text)
        try:
            return body["result"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise MemorySyncError(
                f"Unexpected MCP response format for {tool_name}"
            ) from exc

    @staticmethod
    def _extract_sse_json(body_text: str) -> str:
        parts: list[str] = []
        for raw_line in body_text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            parts.append(data)
        if not parts:
            raise MemorySyncError("SSE response contained no JSON payload")
        return "".join(parts)

    def remember(self, content: str, memory_type: str, project_id: uuid.UUID) -> str:
        return self._call_mcp_tool(
            "remember",
            {
                "content": content,
                "memory_type": memory_type,
                "project_id": str(project_id),
            },
        )

    def pull_memories(
        self,
        project_id: uuid.UUID,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.memory_base_url}/api/memories",
            params={"project_id": str(project_id), "limit": limit},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise MemorySyncError(
                f"Failed to pull memories: status={response.status_code} body={response.text[:200]}"
            )
        body = response.json()
        memories = body.get("memories")
        if not isinstance(memories, list):
            raise MemorySyncError("Unexpected /api/memories response shape")
        return memories


class LocalState:
    def __init__(self, state_dir: Path, *, service_name: str = SERVICE_NAME) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "state.sqlite3"
        self.credentials = CredentialStore(self.state_dir, service_name=service_name)
        self.cipher = self.credentials.load_queue_cipher()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS queue (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  encoded_project TEXT NOT NULL,
                  project_id TEXT NOT NULL,
                  source_file TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  encrypted_payload TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS synced_files (
                  encoded_project TEXT NOT NULL,
                  source_file TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  synced_at TEXT NOT NULL,
                  PRIMARY KEY (encoded_project, source_file)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  occurred_at TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  encoded_project TEXT NOT NULL,
                  source_file TEXT NOT NULL,
                  content_hash TEXT,
                  details_json TEXT NOT NULL
                );
                """
            )

    def record_audit(
        self,
        *,
        event_type: str,
        encoded_project: str,
        source_file: str,
        content_hash_value: str | None,
        details: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                  occurred_at, event_type, encoded_project, source_file, content_hash, details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now_iso(),
                    event_type,
                    encoded_project,
                    source_file,
                    content_hash_value,
                    json.dumps(details, sort_keys=True),
                ),
            )

    def last_synced_hash(self, encoded_project: str, source_file: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT content_hash
                FROM synced_files
                WHERE encoded_project = ? AND source_file = ?
                """,
                (encoded_project, source_file),
            ).fetchone()
        return str(row["content_hash"]) if row else None

    def upsert_synced(self, *, encoded_project: str, source_file: str, content_hash_value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO synced_files (encoded_project, source_file, content_hash, synced_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(encoded_project, source_file)
                DO UPDATE SET content_hash = excluded.content_hash, synced_at = excluded.synced_at
                """,
                (encoded_project, source_file, content_hash_value, now_iso()),
            )

    def enqueue(self, payload: QueuePayload) -> None:
        serialized = json.dumps(payload.__dict__, sort_keys=True)
        encrypted = self.cipher.encrypt(serialized.encode("utf-8")).decode("ascii")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO queue (
                  encoded_project, project_id, source_file, content_hash, encrypted_payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.encoded_project,
                    payload.project_id,
                    payload.source_file,
                    payload.content_hash,
                    encrypted,
                    payload.created_at,
                ),
            )

    def queued_payloads(self) -> list[tuple[int, QueuePayload]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, encrypted_payload FROM queue ORDER BY id ASC"
            ).fetchall()
        payloads: list[tuple[int, QueuePayload]] = []
        for row in rows:
            decrypted = self.cipher.decrypt(str(row["encrypted_payload"]).encode("ascii"))
            data = json.loads(decrypted.decode("utf-8"))
            payloads.append((int(row["id"]), QueuePayload(**data)))
        return payloads

    def delete_queue_item(self, row_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM queue WHERE id = ?", (row_id,))

    def queue_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM queue").fetchone()
        return int(row["total"]) if row else 0

    def known_projects(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT encoded_project FROM queue
                UNION
                SELECT encoded_project FROM synced_files
                UNION
                SELECT encoded_project FROM audit_events
                ORDER BY encoded_project
                """
            ).fetchall()
        return [str(row["encoded_project"]) for row in rows]

    def export_audit_events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT occurred_at, event_type, encoded_project, source_file, content_hash, details_json
                FROM audit_events
                ORDER BY id ASC
                """
            ).fetchall()
        return [
            {
                "occurred_at": str(row["occurred_at"]),
                "event_type": str(row["event_type"]),
                "encoded_project": str(row["encoded_project"]),
                "source_file": str(row["source_file"]),
                "content_hash": row["content_hash"],
                "details": json.loads(str(row["details_json"])),
            }
            for row in rows
        ]


class MemorySyncService:
    def __init__(
        self,
        config: ServiceConfig,
        *,
        cloud: CloudClient | None = None,
        state: LocalState | None = None,
    ) -> None:
        self.config = config
        self.state = state or LocalState(config.state_dir, service_name=config.service_name)
        self.cloud = cloud
        self.config.memory_root.mkdir(parents=True, exist_ok=True)

    def discover_memory_files(self) -> list[Path]:
        pattern = self.config.memory_root.glob("*/memory/*.md")
        return sorted(path for path in pattern if should_intercept_path(path))

    def _safe_unlink(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def process_file(self, path: Path, *, reason: str) -> str:
        raw_content = path.read_text(encoding="utf-8")
        if not raw_content.strip():
            return "skipped"

        parsed = parse_memory_file(raw_content)
        encoded_project = encoded_project_from_path(path)
        hash_value = content_hash(raw_content)
        project_id = project_uuid(encoded_project)
        source_file = path.name

        previous_hash = self.state.last_synced_hash(encoded_project, source_file)
        if previous_hash == hash_value:
            self._safe_unlink(path)
            self.state.record_audit(
                event_type="deduped",
                encoded_project=encoded_project,
                source_file=source_file,
                content_hash_value=hash_value,
                details={"reason": reason},
            )
            return "deduped"

        payload = QueuePayload(
            encoded_project=encoded_project,
            project_id=str(project_id),
            source_file=source_file,
            content_hash=hash_value,
            memory_type=parsed.frontmatter.memory_type or DEFAULT_MEMORY_TYPE,
            raw_content=raw_content,
            created_at=now_iso(),
            reason=reason,
        )

        if self.config.dry_run:
            self.state.record_audit(
                event_type="dry_run",
                encoded_project=encoded_project,
                source_file=source_file,
                content_hash_value=hash_value,
                details={"reason": reason},
            )
            return "dry_run"

        if self.cloud is None:
            raise MemorySyncError("Cloud client is not configured")

        try:
            self.cloud.remember(payload.raw_content, payload.memory_type, project_id)
            self.state.upsert_synced(
                encoded_project=encoded_project,
                source_file=source_file,
                content_hash_value=hash_value,
            )
            self.state.record_audit(
                event_type="persisted",
                encoded_project=encoded_project,
                source_file=source_file,
                content_hash_value=hash_value,
                details={"reason": reason, "memory_type": payload.memory_type},
            )
            self._safe_unlink(path)
            return "persisted"
        except Exception as exc:
            self.state.enqueue(payload)
            self.state.record_audit(
                event_type="queued",
                encoded_project=encoded_project,
                source_file=source_file,
                content_hash_value=hash_value,
                details={"reason": reason, "error": str(exc)},
            )
            self._safe_unlink(path)
            return "queued"

    def flush_queue(self) -> dict[str, int]:
        if self.config.dry_run:
            return {"flushed": 0, "errors": 0}
        if self.cloud is None:
            raise MemorySyncError("Cloud client is not configured")

        report = {"flushed": 0, "errors": 0}
        for row_id, payload in self.state.queued_payloads():
            try:
                self.cloud.remember(
                    payload.raw_content,
                    payload.memory_type,
                    uuid.UUID(payload.project_id),
                )
            except Exception as exc:
                report["errors"] += 1
                self.state.record_audit(
                    event_type="flush_failed",
                    encoded_project=payload.encoded_project,
                    source_file=payload.source_file,
                    content_hash_value=payload.content_hash,
                    details={"error": str(exc)},
                )
                continue

            self.state.delete_queue_item(row_id)
            self.state.upsert_synced(
                encoded_project=payload.encoded_project,
                source_file=payload.source_file,
                content_hash_value=payload.content_hash,
            )
            self.state.record_audit(
                event_type="flushed",
                encoded_project=payload.encoded_project,
                source_file=payload.source_file,
                content_hash_value=payload.content_hash,
                details={"queued_at": payload.created_at, "reason": payload.reason},
            )
            report["flushed"] += 1
        return report

    def _render_project_memories(self, encoded_project: str) -> str:
        queued = self.state.queue_count()
        project_id = project_uuid(encoded_project)
        if self.cloud is None:
            return self._placeholder_memory_index(queued)

        try:
            memories = self.cloud.pull_memories(project_id)
        except Exception:
            return self._placeholder_memory_index(queued)

        visible = [
            memory
            for memory in memories
            if memory.get("memory_type") != AUDIT_MEMORY_TYPE
        ]
        visible.sort(
            key=lambda item: item.get("updated_at") or item.get("created_at") or "",
            reverse=True,
        )
        if not visible:
            return self._placeholder_memory_index(queued)

        lines = [
            "# Memory Index",
            "",
            "This file is rendered from SerenDB by `claude/serendb-memory`.",
            f"Encoded project: `{encoded_project}`",
            "",
        ]
        for memory in visible:
            parsed = parse_memory_file(str(memory.get("content", "")))
            title = parsed.frontmatter.name or memory.get("memory_type") or DEFAULT_MEMORY_TYPE
            description = parsed.frontmatter.description
            lines.append(f"## {title}")
            lines.append("")
            lines.append(f"- Type: `{memory.get('memory_type') or DEFAULT_MEMORY_TYPE}`")
            if description:
                lines.append(f"- Description: {description}")
            updated_at = memory.get("updated_at") or memory.get("created_at")
            if updated_at:
                lines.append(f"- Updated: {updated_at}")
            lines.append("")
            body = parsed.body or str(memory.get("content", "")).strip()
            lines.append(body or "_No content_")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _placeholder_memory_index(queued: int) -> str:
        lines = [
            "# Memory Index",
            "",
            "Claude memory is managed by SerenDB.",
            "The local watcher has not hydrated cloud memory yet.",
        ]
        if queued:
            lines.append(f"{queued} encrypted queue item(s) are waiting to flush.")
        return "\n".join(lines).rstrip() + "\n"

    def render_all_indexes(self) -> int:
        rendered = 0
        project_names = set(self.state.known_projects())
        project_names.update(
            child.name
            for child in self.config.memory_root.iterdir()
            if child.is_dir() and (child / "memory").exists()
        )
        for encoded_project in sorted(project_names):
            project_dir = self.config.memory_root / encoded_project
            rendered_md = self._render_project_memories(encoded_project)
            write_atomic(project_dir / MEMORY_INDEX_FILENAME, rendered_md)
            rendered += 1
        return rendered

    def sync_once(self) -> dict[str, int]:
        report = {
            "processed": 0,
            "persisted": 0,
            "queued": 0,
            "deduped": 0,
            "dry_run": 0,
            "flushed": 0,
            "flush_errors": 0,
            "rendered": 0,
        }
        for path in self.discover_memory_files():
            report["processed"] += 1
            outcome = self.process_file(path, reason="scan")
            if outcome in report:
                report[outcome] += 1
        flushed = self.flush_queue()
        report["flushed"] = flushed["flushed"]
        report["flush_errors"] = flushed["errors"]
        report["rendered"] = self.render_all_indexes()
        return report

    def install_service(self, *, config_path: Path, python_executable: str, agent_path: Path) -> dict[str, Any]:
        service_path = service_definition_path(self.config.service_name)
        service_path.parent.mkdir(parents=True, exist_ok=True)
        if platform.system() == "Darwin":
            service_path.write_text(
                build_launchagent_plist(
                    label=self.config.service_name,
                    python_executable=python_executable,
                    agent_path=agent_path,
                    config_path=config_path,
                    state_dir=self.config.state_dir,
                ),
                encoding="utf-8",
            )
            commands = [
                ["launchctl", "unload", str(service_path)],
                ["launchctl", "load", str(service_path)],
            ]
        elif platform.system() == "Linux":
            service_path.write_text(
                build_systemd_unit(
                    label=self.config.service_name,
                    python_executable=python_executable,
                    agent_path=agent_path,
                    config_path=config_path,
                    state_dir=self.config.state_dir,
                ),
                encoding="utf-8",
            )
            commands = [
                ["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", f"{self.config.service_name}.service"],
            ]
        else:
            return {
                "status": "warning",
                "service_path": str(service_path),
                "message": "Automatic background service install is only implemented for macOS and Linux.",
            }

        command_results: list[dict[str, Any]] = []
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            command_results.append(
                {
                    "command": command,
                    "returncode": result.returncode,
                    "stderr": result.stderr.strip(),
                    "stdout": result.stdout.strip(),
                }
            )
        return {"status": "ok", "service_path": str(service_path), "commands": command_results}

    def stop_service(self) -> dict[str, Any]:
        if platform.system() == "Darwin":
            definition = service_definition_path(self.config.service_name)
            command = ["launchctl", "unload", str(definition)]
        elif platform.system() == "Linux":
            command = ["systemctl", "--user", "stop", f"{self.config.service_name}.service"]
        else:
            return {"status": "warning", "message": "No managed service for this platform."}
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    def start_service(self) -> dict[str, Any]:
        if platform.system() == "Darwin":
            definition = service_definition_path(self.config.service_name)
            command = ["launchctl", "load", str(definition)]
        elif platform.system() == "Linux":
            command = ["systemctl", "--user", "start", f"{self.config.service_name}.service"]
        else:
            return {"status": "warning", "message": "No managed service for this platform."}
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }

    def uninstall_service(self) -> dict[str, Any]:
        stop_result = self.stop_service()
        definition = service_definition_path(self.config.service_name)
        if definition.exists():
            definition.unlink()
        if platform.system() == "Linux":
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True,
                text=True,
                check=False,
            )
        return {
            "status": "ok",
            "stopped": stop_result,
            "service_path": str(definition),
            "removed": not definition.exists(),
        }

    def service_status(self) -> dict[str, Any]:
        definition = service_definition_path(self.config.service_name)
        installed = definition.exists()
        running = False
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["launchctl", "list", self.config.service_name],
                capture_output=True,
                text=True,
                check=False,
            )
            running = result.returncode == 0
        elif platform.system() == "Linux":
            result = subprocess.run(
                ["systemctl", "--user", "is-active", f"{self.config.service_name}.service"],
                capture_output=True,
                text=True,
                check=False,
            )
            running = result.returncode == 0 and result.stdout.strip() == "active"
        return {
            "installed": installed,
            "running": running,
            "service_path": str(definition),
        }

    def doctor(self) -> dict[str, Any]:
        api_key, source = self.state.credentials.read_api_key()
        checks = [
            {
                "name": "memory_root_exists",
                "ok": self.config.memory_root.exists(),
                "value": str(self.config.memory_root),
            },
            {
                "name": "state_dir_exists",
                "ok": self.config.state_dir.exists(),
                "value": str(self.config.state_dir),
            },
            {
                "name": "api_key_available",
                "ok": bool(api_key),
                "value": source or "missing",
            },
            {
                "name": "service_status",
                "ok": True,
                "value": self.service_status(),
            },
            {
                "name": "queue_count",
                "ok": True,
                "value": self.state.queue_count(),
            },
        ]
        return {"status": "ok", "checks": checks}

    def export_memories(self, output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for encoded_project in self.state.known_projects():
            project_dir = output_dir / encoded_project
            project_dir.mkdir(parents=True, exist_ok=True)
            rendered = self._render_project_memories(encoded_project)
            write_atomic(project_dir / MEMORY_INDEX_FILENAME, rendered)
            written += 1
        audit_path = output_dir / "audit-events.json"
        audit_path.write_text(
            json.dumps(self.state.export_audit_events(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "projects_exported": written, "audit_path": str(audit_path)}


def build_service_config(config_data: dict[str, Any]) -> ServiceConfig:
    inputs = config_data.get("inputs", {})
    service = config_data.get("service", {})
    memory = config_data.get("memory", {})
    return ServiceConfig(
        memory_root=expand_path(str(inputs.get("memory_root", "~/.claude/projects"))),
        state_dir=expand_path(str(inputs.get("state_dir", "~/.seren/claude-serendb-memory"))),
        api_base_url=str(memory.get("api_base_url", "https://api.serendb.com")),
        memory_base_url=str(memory.get("memory_base_url", "https://memory.serendb.com")),
        poll_interval_seconds=int(inputs.get("poll_interval_seconds", 3)),
        dry_run=bool(config_data.get("dry_run", False)),
        install_service_on_install=bool(service.get("install_on_install", True)),
        start_after_install=bool(service.get("start_after_install", True)),
        auto_register_key=bool(service.get("auto_register_key", True)),
        timeout_seconds=int(service.get("timeout_seconds", 5)),
    )


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_default_config(path: Path) -> Path:
    body = {
        "skill": "serendb-memory",
        "dry_run": False,
        "inputs": {
            "command": "status",
            "memory_root": "~/.claude/projects",
            "state_dir": "~/.seren/claude-serendb-memory",
            "poll_interval_seconds": 3,
        },
        "memory": {
            "api_base_url": "https://api.serendb.com",
            "memory_base_url": "https://memory.serendb.com",
        },
        "service": {
            "install_on_install": True,
            "start_after_install": True,
            "auto_register_key": True,
            "timeout_seconds": 5,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def ensure_cloud_client(config: ServiceConfig, state: LocalState) -> tuple[SerenCloudClient, str]:
    api_key, source = state.credentials.read_api_key()
    if api_key:
        client = SerenCloudClient(
            api_key=api_key,
            api_base_url=config.api_base_url,
            memory_base_url=config.memory_base_url,
            timeout_seconds=config.timeout_seconds,
        )
        if client.validate_api_key():
            return client, source or "unknown"
    if not config.auto_register_key:
        raise MemorySyncError("SEREN_API_KEY is missing or invalid and auto-registration is disabled.")

    bootstrap_client = SerenCloudClient(
        api_key="",
        api_base_url=config.api_base_url,
        memory_base_url=config.memory_base_url,
        timeout_seconds=config.timeout_seconds,
    )
    api_key = bootstrap_client.create_api_key(name=SERVICE_NAME)
    stored_via = state.credentials.store_api_key(api_key)
    client = SerenCloudClient(
        api_key=api_key,
        api_base_url=config.api_base_url,
        memory_base_url=config.memory_base_url,
        timeout_seconds=config.timeout_seconds,
    )
    return client, f"auto-registered:{stored_via}"


def build_launchagent_plist(
    *,
    label: str,
    python_executable: str,
    agent_path: Path,
    config_path: Path,
    state_dir: Path,
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
      <string>{python_executable}</string>
      <string>{agent_path}</string>
      <string>start</string>
      <string>--foreground</string>
      <string>--config</string>
      <string>{config_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{agent_path.parent.parent}</string>
    <key>StandardOutPath</key>
    <string>{state_dir / "service.stdout.log"}</string>
    <key>StandardErrorPath</key>
    <string>{state_dir / "service.stderr.log"}</string>
  </dict>
</plist>
"""


def build_systemd_unit(
    *,
    label: str,
    python_executable: str,
    agent_path: Path,
    config_path: Path,
    state_dir: Path,
) -> str:
    return f"""[Unit]
Description=Claude Code SerenDB memory watcher
After=network-online.target

[Service]
Type=simple
ExecStart={python_executable} {agent_path} start --foreground --config {config_path}
WorkingDirectory={agent_path.parent.parent}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:{state_dir / "service.stdout.log"}
StandardError=append:{state_dir / "service.stderr.log"}

[Install]
WantedBy=default.target
"""


def service_definition_path(label: str) -> Path:
    if platform.system() == "Darwin":
        return expand_path(f"~/Library/LaunchAgents/com.seren.{label}.plist")
    if platform.system() == "Linux":
        return expand_path(f"~/.config/systemd/user/{label}.service")
    return expand_path(f"~/.config/{label}.service")
