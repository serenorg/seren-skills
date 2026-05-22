"""Critical tests for scripts/cron/seren_cron_client.py.

The client is a thin wrapper over `scripts.seren_client.call_publisher`
that talks to the seren-cron publisher. Tests pin the *publisher call
shape* — wrong path or wrong body shape breaks the cron silently, so
this is the load-bearing surface. No tests for the urllib transport;
that is already covered in test_seren_client.py.
"""

from __future__ import annotations

from typing import Any

from scripts.cron import seren_cron_client as scc


class _StubCaller:
    """Records every (publisher, method, path, body) tuple and returns
    the next queued response. Mirrors the `fetcher` seam used in
    test_seren_client.py — but at the `call_publisher` layer because the
    cron client is built on top of it.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, Any]] = []
        self.responses: list[Any] = []

    def queue(self, response: Any) -> None:
        self.responses.append(response)

    def __call__(
        self,
        publisher: str,
        method: str,
        path: str,
        *,
        body: Any = None,
    ) -> Any:
        self.calls.append((publisher, method, path, body))
        if not self.responses:
            raise AssertionError(
                f"No queued response for {method} {path}; "
                f"previous calls: {self.calls}"
            )
        return self.responses.pop(0)


def _make_client() -> tuple[scc.SerenCronClient, _StubCaller]:
    stub = _StubCaller()
    return scc.SerenCronClient(call=stub), stub


def test_list_runners_hits_api_runners_get() -> None:
    client, stub = _make_client()
    stub.queue([{"id": "r1", "name": "pk-mac"}])

    runners = client.list_runners()

    assert runners == [{"id": "r1", "name": "pk-mac"}]
    assert stub.calls == [("seren-cron", "GET", "/api/runners", None)]


def test_ensure_runner_is_idempotent_on_name() -> None:
    """A second create with the same name must not POST a new runner.

    The runner is the per-host identity; duplicating it would split the
    job-claim space and leak ticks.
    """

    client, stub = _make_client()
    stub.queue([{"id": "r1", "name": "pk-lead-intelligence-mac"}])

    runner = client.ensure_runner(
        runner_name="pk-lead-intelligence-mac",
        machine_label="mac",
        platform_label="darwin-arm64",
        poll_interval_seconds=43200,
    )

    assert runner["id"] == "r1"
    assert stub.calls == [("seren-cron", "GET", "/api/runners", None)]


def test_ensure_runner_posts_when_missing() -> None:
    client, stub = _make_client()
    stub.queue([])  # list_runners returns empty
    stub.queue({"id": "r-new", "name": "pk-lead-intelligence-mac"})

    runner = client.ensure_runner(
        runner_name="pk-lead-intelligence-mac",
        machine_label="mac",
        platform_label="darwin-arm64",
        poll_interval_seconds=43200,
    )

    assert runner["id"] == "r-new"
    assert stub.calls[0] == ("seren-cron", "GET", "/api/runners", None)
    publisher, method, path, body = stub.calls[1]
    assert (publisher, method, path) == ("seren-cron", "POST", "/api/runners")
    assert body["name"] == "pk-lead-intelligence-mac"
    assert body["skill_slug"] == "pk-lead-intelligence"
    assert body["poll_interval_seconds"] == 43200


def test_upsert_local_pull_job_creates_when_absent() -> None:
    client, stub = _make_client()
    stub.queue([])  # list_jobs empty
    stub.queue({"id": "j1", "name": "pk-lead-intelligence-daily"})

    job = client.upsert_local_pull_job(
        name="pk-lead-intelligence-daily",
        runner_id="r1",
        cron_expression="0 6 * * 1-5",
        timezone_name="America/New_York",
        local_payload={"command": "run", "flags": ["--batch"]},
    )

    assert job["id"] == "j1"
    publisher, method, path, body = stub.calls[1]
    assert (publisher, method, path) == ("seren-cron", "POST", "/api/jobs")
    assert body["execution_mode"] == "local_pull"
    assert body["cron_expression"] == "0 6 * * 1-5"
    assert body["timezone"] == "America/New_York"
    assert body["runner_id"] == "r1"
    assert body["local_payload"]["command"] == "run"


def test_upsert_local_pull_job_updates_when_present() -> None:
    """Idempotent re-create: same job name → PUT, not POST."""

    client, stub = _make_client()
    stub.queue([{"id": "j1", "name": "pk-lead-intelligence-daily"}])
    stub.queue({"id": "j1", "name": "pk-lead-intelligence-daily", "updated": True})

    client.upsert_local_pull_job(
        name="pk-lead-intelligence-daily",
        runner_id="r1",
        cron_expression="0 6 * * 1-5",
        timezone_name="America/New_York",
        local_payload={"command": "run"},
    )

    assert stub.calls[1][:3] == ("seren-cron", "PUT", "/api/jobs/j1")


def test_poll_returns_normalized_result() -> None:
    client, stub = _make_client()
    stub.queue(
        {
            "action": "run",
            "next_poll_seconds": 90,
            "job": {"id": "j1", "local_payload": {"command": "run"}},
            "execution_result": {"id": "exec-42", "status": "running"},
        }
    )

    result = client.poll("r1", last_seen_result_id=None)

    assert result.action == "run"
    assert result.execution_result_id == "exec-42"
    assert result.next_poll_seconds == 90
    assert result.job["id"] == "j1"
    publisher, method, path, body = stub.calls[0]
    assert (publisher, method, path) == ("seren-cron", "POST", "/api/runners/r1/poll")
    assert body["last_seen_result_id"] is None
    assert body["supports"]["stdout_tail"] is True


def test_submit_result_carries_status_and_tails() -> None:
    client, stub = _make_client()
    stub.queue({"updated": True})

    client.submit_result(
        "r1",
        execution_result_id="exec-42",
        status="succeeded",
        response_body='{"status":"ok"}',
        exit_code=0,
        stdout_tail="run summary",
        stderr_tail="",
    )

    publisher, method, path, body = stub.calls[0]
    assert (publisher, method, path) == (
        "seren-cron",
        "POST",
        "/api/runners/r1/results",
    )
    assert body["execution_result_id"] == "exec-42"
    assert body["status"] == "succeeded"
    assert body["exit_code"] == 0
    assert body["stdout_tail"] == "run summary"


def test_pause_resume_delete_job_use_expected_paths() -> None:
    client, stub = _make_client()
    stub.queue({"id": "j1", "status": "paused"})
    stub.queue({"id": "j1", "status": "active"})
    stub.queue({"id": "j1", "status": "deleted"})

    client.pause_job("j1")
    client.resume_job("j1")
    client.delete_job("j1")

    assert [(m, p) for (_, m, p, _) in stub.calls] == [
        ("POST", "/api/jobs/j1/pause"),
        ("POST", "/api/jobs/j1/resume"),
        ("DELETE", "/api/jobs/j1"),
    ]
