"""Critical tests for scripts/run_local_pull_runner.py.

Two load-bearing paths:

  1. Happy path: poll → action=run → subprocess agent.py → submit result.
     Wrong subprocess argv breaks the cron silently, so we pin it.
  2. Auto-pause on publisher 402 (low SerenBucks). The runner must
     pause the job before the next tick so a cron with no SerenBucks
     does not burn the operator's prepaid balance with retries.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import scripts.run_local_pull_runner as runner
from scripts.cron import seren_cron_client as scc


class _FakeClient:
    def __init__(self) -> None:
        self.poll_calls: list[tuple[str, Any]] = []
        self.submit_calls: list[dict[str, Any]] = []
        self.paused_jobs: list[str] = []
        self._poll_queue: list[scc.PollResult] = []
        self._next_runner = {"id": "r1", "name": "runner-x"}

    def queue_poll(self, result: scc.PollResult) -> None:
        self._poll_queue.append(result)

    def ensure_runner(self, **kwargs: Any) -> dict[str, Any]:
        return self._next_runner

    def poll(
        self, runner_id: str, *, last_seen_result_id: str | None = None
    ) -> scc.PollResult:
        self.poll_calls.append((runner_id, last_seen_result_id))
        if not self._poll_queue:
            return scc.PollResult(
                action="idle", job={}, execution_result_id="", next_poll_seconds=30
            )
        return self._poll_queue.pop(0)

    def submit_result(self, runner_id: str, **kwargs: Any) -> Any:
        self.submit_calls.append({"runner_id": runner_id, **kwargs})
        return {"updated": True}

    def pause_job(self, job_id: str) -> Any:
        self.paused_jobs.append(job_id)
        return {"id": job_id, "status": "paused"}


class _FakeCompleted:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_once_happy_path_dispatches_agent_and_submits_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient()
    fake_client.queue_poll(
        scc.PollResult(
            action="run",
            job={
                "id": "j1",
                "local_payload": {
                    "skill_slug": "pk-lead-intelligence",
                    "command": "run",
                    "flags": ["--dry-run", "--batch"],
                    "config_path": "config.json",
                },
            },
            execution_result_id="exec-1",
            next_poll_seconds=30,
        )
    )

    monkeypatch.setattr(runner, "_build_client", lambda: fake_client)

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompleted:
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        return _FakeCompleted(
            stdout=(
                "pk-lead-intelligence run: command=run dry_run=true "
                "leads_evaluated=3 notes_written=0 notes_skipped_non_pk=0 "
                "notes_skipped_recent=0 docx_written=3 leads_failed=0\n"
            )
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = runner.main(["--once"])
    assert exit_code == 0

    # Pinned argv shape: python <agent.py> --config config.json --command run --dry-run --batch
    argv = captured["argv"]
    assert argv[0].endswith("python") or argv[0].endswith("python3") or argv[0] == runner.sys.executable
    assert any(arg.endswith("agent.py") for arg in argv)
    assert "--command" in argv and argv[argv.index("--command") + 1] == "run"
    assert "--config" in argv and argv[argv.index("--config") + 1] == "config.json"
    assert "--dry-run" in argv
    assert "--batch" in argv

    # Result was submitted with succeeded status.
    assert len(fake_client.submit_calls) == 1
    submitted = fake_client.submit_calls[0]
    assert submitted["execution_result_id"] == "exec-1"
    assert submitted["status"] == "succeeded"
    assert submitted["exit_code"] == 0
    assert "leads_evaluated=3" in submitted["stdout_tail"]

    # No auto-pause on a healthy tick.
    assert fake_client.paused_jobs == []


def test_once_auto_pauses_on_publisher_402(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publisher 402 (low SerenBucks) must pause the job, not silently
    retry on the next tick.

    The agent.py subprocess prints `Publisher returned HTTP 402` on
    stderr when seren_client.PublisherError fires. The runner detects
    this and calls pause_job before exiting.
    """

    fake_client = _FakeClient()
    fake_client.queue_poll(
        scc.PollResult(
            action="run",
            job={
                "id": "j1",
                "local_payload": {
                    "skill_slug": "pk-lead-intelligence",
                    "command": "run",
                    "flags": ["--allow-live", "--batch"],
                    "config_path": "config.json",
                },
            },
            execution_result_id="exec-2",
            next_poll_seconds=30,
        )
    )

    monkeypatch.setattr(runner, "_build_client", lambda: fake_client)

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompleted:
        return _FakeCompleted(
            stdout="",
            stderr="Publisher returned HTTP 402: insufficient SerenBucks\n",
            returncode=1,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = runner.main(["--once"])
    # The runner returns the subprocess exit code on --once so cron logs
    # surface the failure.
    assert exit_code == 1

    assert fake_client.paused_jobs == ["j1"], (
        f"Expected j1 paused on 402; saw {fake_client.paused_jobs}"
    )
    submitted = fake_client.submit_calls[0]
    assert submitted["status"] == "failed"
