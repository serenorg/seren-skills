"""Phase 12 cron plumbing tests (plan §18.5).

Critical-only scope: four assertions covering setup payload shape,
runner subprocess dispatch, and the two auto-pause guards (pool
exhausted, low SerenBucks). Everything else in §18 is plumbing on top
of polymarket-bot's reference implementation and does not warrant a
dedicated test.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from run_local_pull_runner import execute_one_tick
from seren_cron_client import PollResult, SerenCronClient
from setup_cron import parse_args as setup_cron_parse_args
from setup_cron import run_create


@pytest.fixture
def cron_gateway(stub_gateway):
    """Pre-register the seren-cron responses every cron test needs."""
    stub_gateway.register("seren-cron", "GET", "/api/runners", [])
    stub_gateway.register(
        "seren-cron",
        "POST",
        "/api/runners",
        {"id": "runner_abc", "name": "prophet-bounty-runner-laptop", "skill_slug": "prophet-bounty-runner"},
    )
    stub_gateway.register("seren-cron", "GET", "/api/jobs", [])
    stub_gateway.register(
        "seren-cron",
        "POST",
        "/api/jobs",
        {"id": "job_xyz", "name": "prophet-bounty-runner-deadbeef"},
    )
    return stub_gateway


def _find_call(gateway, method: str, path: str) -> dict[str, Any]:
    matches = [c for c in gateway.calls if c["method"] == method and c["path"] == path]
    assert matches, f"{method} {path} was never called; calls={gateway.calls}"
    return matches[-1]


# Test 1 — setup_cron create passes user inputs to seren-cron
# Plan §18.5: verifies prophet_email/email_provider land in the local_payload
# the runner will replay every 6h.

def test_setup_cron_create_passes_user_inputs_to_seren_cron(cron_gateway):
    args = setup_cron_parse_args(
        [
            "create",
            "--config", "config.json",
            "--prophet-email", "user@example.com",
            "--email-provider", "outlook",
            "--bounty-id", "bounty_test_123",
            "--user-id-short", "deadbeef",
            "--machine-label", "laptop",
        ]
    )
    client = SerenCronClient(gateway=cron_gateway)

    result = run_create(client, args)

    assert result["runner"]["id"] == "runner_abc"
    assert result["job"]["id"] == "job_xyz"

    job_post = _find_call(cron_gateway, "POST", "/api/jobs")
    body = job_post["body"]
    assert body["name"] == "prophet-bounty-runner-deadbeef"
    assert body["cron_expression"] == "0 */6 * * *"
    assert body["timezone"] == "UTC"
    assert body["execution_mode"] == "local_pull"
    assert body["runner_id"] == "runner_abc"

    payload = body["local_payload"]
    assert payload["skill_slug"] == "prophet-bounty-runner"
    assert payload["command"] == "run"
    assert payload["prophet_email"] == "user@example.com"
    assert payload["email_provider"] == "outlook"
    assert payload["bounty_id"] == "bounty_test_123"
    assert payload["json_output"] is True
    assert payload["dry_run"] is False


# Test 2 — runner executes agent.py with the run command when a job is claimed
# Plan §18.5: verifies the polled local_payload becomes the right argv.

def test_runner_executes_agent_py_with_run_command_when_job_is_claimed(cron_gateway):
    cron_gateway.register("seren-cron", "POST", "/api/runners/runner_abc/results", {"ok": True})
    captured: dict[str, Any] = {}

    def fake_subprocess(cmd, cwd=None, capture_output=False, text=False):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return SimpleNamespace(stdout='{"status": "ok", "command": "run"}', stderr="", returncode=0)

    poll_result = PollResult(
        action="run",
        job={
            "id": "job_xyz",
            "local_payload": {
                "skill_slug": "prophet-bounty-runner",
                "command": "run",
                "config_path": "config.json",
                "prophet_email": "user@example.com",
                "email_provider": "gmail",
                "json_output": True,
                "dry_run": False,
            },
        },
        execution_result_id="result_001",
        next_poll_seconds=30,
    )

    summary = execute_one_tick(
        poll_result,
        client=SerenCronClient(gateway=cron_gateway),
        runner_id="runner_abc",
        default_config="config.json",
        skill_root=Path("/tmp/skill_root"),
        subprocess_runner=fake_subprocess,
    )

    cmd = captured["cmd"]
    assert "--command" in cmd
    assert cmd[cmd.index("--command") + 1] == "run"
    assert "--prophet-email" in cmd and cmd[cmd.index("--prophet-email") + 1] == "user@example.com"
    assert "--email-provider" in cmd and cmd[cmd.index("--email-provider") + 1] == "gmail"
    assert "--json-output" in cmd
    assert "--dry-run" not in cmd
    assert summary["action"] == "run"
    assert summary["exit_code"] == 0
    assert summary["paused"] is False

    submit = _find_call(cron_gateway, "POST", "/api/runners/runner_abc/results")
    assert submit["body"]["status"] == "succeeded"
    assert submit["body"]["execution_result_id"] == "result_001"


# Test 3 — pool exhaustion auto-pauses the cron job
# Plan §18.3: blocked_no_bounty signals the pool is dry; the runner pauses
# the job before submitting the result.

def test_pool_exhausted_pauses_cron_job(cron_gateway):
    cron_gateway.register("seren-cron", "POST", "/api/jobs/job_xyz/pause", {"paused": True})
    cron_gateway.register("seren-cron", "POST", "/api/runners/runner_abc/results", {"ok": True})

    def fake_subprocess(cmd, cwd=None, capture_output=False, text=False):
        return SimpleNamespace(
            stdout='{"status": "blocked", "command": "run", "reason": "blocked_no_bounty"}',
            stderr="",
            returncode=1,
        )

    poll_result = PollResult(
        action="run",
        job={"id": "job_xyz", "local_payload": {"prophet_email": "u@x.com", "email_provider": "gmail"}},
        execution_result_id="result_002",
        next_poll_seconds=30,
    )

    summary = execute_one_tick(
        poll_result,
        client=SerenCronClient(gateway=cron_gateway),
        runner_id="runner_abc",
        default_config="config.json",
        subprocess_runner=fake_subprocess,
    )

    assert summary["paused"] is True
    assert summary["auto_pause_reason"] == "pool_exhausted"

    pause = _find_call(cron_gateway, "POST", "/api/jobs/job_xyz/pause")
    submit = _find_call(cron_gateway, "POST", "/api/runners/runner_abc/results")
    # Pause must precede submit so the dashboard reflects paused state.
    assert cron_gateway.calls.index(pause) < cron_gateway.calls.index(submit)
    assert submit["body"]["status"] == "failed"


# Test 4 — publisher 402 in stderr auto-pauses the cron job
# Plan §18.3: low SerenBucks surfaces as a publisher 402; runner must pause.

def test_low_serenbucks_pauses_cron_job(cron_gateway):
    cron_gateway.register("seren-cron", "POST", "/api/jobs/job_xyz/pause", {"paused": True})
    cron_gateway.register("seren-cron", "POST", "/api/runners/runner_abc/results", {"ok": True})

    def fake_subprocess(cmd, cwd=None, capture_output=False, text=False):
        return SimpleNamespace(
            stdout="",
            stderr="seren publisher request failed: status: 402 payment required",
            returncode=1,
        )

    poll_result = PollResult(
        action="run",
        job={"id": "job_xyz", "local_payload": {"prophet_email": "u@x.com", "email_provider": "gmail"}},
        execution_result_id="result_003",
        next_poll_seconds=30,
    )

    summary = execute_one_tick(
        poll_result,
        client=SerenCronClient(gateway=cron_gateway),
        runner_id="runner_abc",
        default_config="config.json",
        subprocess_runner=fake_subprocess,
    )

    assert summary["paused"] is True
    assert summary["auto_pause_reason"] == "low_serenbucks"
    pause = _find_call(cron_gateway, "POST", "/api/jobs/job_xyz/pause")
    assert pause["body"] == {}
