from __future__ import annotations

from pathlib import Path
from typing import Any

from common import dump_json, load_json, utc_now_iso


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = load_json(
            path,
            {
                "run_id": None,
                "completed_steps": [],
                "step_payloads": {},
                "updated_at": None,
                "status": "running",
                "error_code": None,
            },
        )

    def save(self) -> None:
        self.state["updated_at"] = utc_now_iso()
        dump_json(self.path, self.state)

    def start_run(self, run_id: str) -> None:
        if self.state.get("run_id") != run_id:
            self.state = {
                "run_id": run_id,
                "completed_steps": [],
                "step_payloads": {},
                "updated_at": utc_now_iso(),
                "status": "running",
                "error_code": None,
            }
            self.save()

    def set_status(self, status: str, error_code: str | None = None) -> None:
        self.state["status"] = status
        self.state["error_code"] = error_code
        self.save()

    def is_completed(self, step: str) -> bool:
        return step in self.state.get("completed_steps", [])

    def mark_complete(self, step: str, payload: dict[str, Any] | None = None) -> None:
        if step not in self.state["completed_steps"]:
            self.state["completed_steps"].append(step)
        if payload is not None:
            self.state["step_payloads"][step] = payload
        self.save()

    def payload(self, step: str) -> dict[str, Any] | None:
        return self.state.get("step_payloads", {}).get(step)
