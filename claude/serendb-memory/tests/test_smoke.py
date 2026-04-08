from __future__ import annotations

import importlib.util
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    module_path = SKILL_DIR / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib guard
        raise RuntimeError(f"Could not load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


memory_service = _load_module("memory_service", "scripts/memory_service.py")
agent = _load_module("serendb_memory_agent", "scripts/agent.py")


class FakeCloudClient:
    def __init__(self, *, fail_remember: bool = False) -> None:
        self.fail_remember = fail_remember
        self.remember_calls: list[dict[str, str]] = []
        self.memories_by_project: dict[str, list[dict[str, str]]] = {}

    def remember(self, content: str, memory_type: str, project_id: uuid.UUID) -> str:
        if self.fail_remember:
            raise RuntimeError("memory api unavailable")
        payload = {
            "content": content,
            "memory_type": memory_type,
            "project_id": str(project_id),
            "updated_at": "2026-04-08T00:00:00Z",
        }
        self.remember_calls.append(payload)
        self.memories_by_project.setdefault(str(project_id), []).append(payload)
        return "ok"

    def pull_memories(self, project_id: uuid.UUID, *, limit: int = 200) -> list[dict[str, str]]:
        return list(self.memories_by_project.get(str(project_id), []))[:limit]


def _build_service(tmp_path: Path, *, cloud: FakeCloudClient):
    memory_root = tmp_path / ".claude" / "projects"
    state_dir = tmp_path / ".seren" / "claude-serendb-memory"
    config = memory_service.ServiceConfig(
        memory_root=memory_root,
        state_dir=state_dir,
        dry_run=False,
    )
    state = memory_service.LocalState(state_dir, service_name="test-serendb-memory")
    service = memory_service.MemorySyncService(config, cloud=cloud, state=state)
    return service, state, memory_root


def _write_memory_file(memory_root: Path, *, encoded_project: str, name: str, contents: str) -> Path:
    target = memory_root / encoded_project / "memory" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    return target


def test_cli_blocks_seren_desktop_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEREN_HOST", "seren-desktop")

    result = agent.run_once(config_path=str(tmp_path / "config.json"), command="status")

    assert result["status"] == "error"
    assert result["error_code"] == "desktop_not_supported"
    assert "SEREN_HOST=seren-desktop" in result["message"]


def test_sync_persists_deletes_source_and_renders_memory_index(tmp_path: Path) -> None:
    cloud = FakeCloudClient()
    service, state, memory_root = _build_service(tmp_path, cloud=cloud)
    encoded_project = "Users-taariq-projects-demo"
    source = _write_memory_file(
        memory_root,
        encoded_project=encoded_project,
        name="preferences.md",
        contents=(
            "---\n"
            "name: Working Style\n"
            "description: Team preferences\n"
            "type: claude_preference\n"
            "---\n"
            "Prefer concise pull request summaries.\n"
        ),
    )

    first_report = service.sync_once()

    assert first_report["processed"] == 1
    assert first_report["persisted"] == 1
    assert first_report["queued"] == 0
    assert not source.exists()
    assert len(cloud.remember_calls) == 1
    assert state.queue_count() == 0

    memory_index = memory_root / encoded_project / "MEMORY.md"
    rendered = memory_index.read_text(encoding="utf-8")
    assert "Working Style" in rendered
    assert "Prefer concise pull request summaries." in rendered

    duplicate = _write_memory_file(
        memory_root,
        encoded_project=encoded_project,
        name="preferences.md",
        contents=(
            "---\n"
            "name: Working Style\n"
            "description: Team preferences\n"
            "type: claude_preference\n"
            "---\n"
            "Prefer concise pull request summaries.\n"
        ),
    )

    second_report = service.sync_once()

    assert second_report["processed"] == 1
    assert second_report["deduped"] == 1
    assert len(cloud.remember_calls) == 1
    assert not duplicate.exists()


def test_failed_persist_is_encrypted_locally_then_flushes(tmp_path: Path) -> None:
    failing_cloud = FakeCloudClient(fail_remember=True)
    service, state, memory_root = _build_service(tmp_path, cloud=failing_cloud)
    encoded_project = "Users-taariq-projects-offline"
    source = _write_memory_file(
        memory_root,
        encoded_project=encoded_project,
        name="offline.md",
        contents="Sensitive preference that should never remain plaintext locally.\n",
    )

    report = service.sync_once()

    assert report["queued"] == 1
    assert report["flush_errors"] == 1
    assert not source.exists()
    assert state.queue_count() == 1

    connection = sqlite3.connect(state.db_path)
    row = connection.execute("SELECT encrypted_payload FROM queue").fetchone()
    connection.close()
    assert row is not None
    assert "Sensitive preference" not in row[0]

    working_cloud = FakeCloudClient()
    service.cloud = working_cloud

    flush_report = service.flush_queue()

    assert flush_report == {"flushed": 1, "errors": 0}
    assert state.queue_count() == 0
    assert len(working_cloud.remember_calls) == 1
