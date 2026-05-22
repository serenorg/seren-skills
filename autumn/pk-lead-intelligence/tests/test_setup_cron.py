"""Critical tests for scripts/setup_cron.py.

The operator-facing CLI for the Phase 5 scheduler. Tests pin the two
load-bearing properties:

  1. `create --job daily|weekly` builds the right local_payload and
     cron expression — wrong payload here means the runner dispatches
     the wrong agent.py command.
  2. Idempotency: a second `create --job daily` does not duplicate the
     job. The client layer already guarantees this on the publisher
     side, but we verify the CLI exercises that path.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import scripts.setup_cron as setup_cron
from scripts.cron import seren_cron_client as scc


class _FakeClient:
    """Records what the CLI asks the client to do without hitting the
    network. Mirrors the SerenCronClient surface the CLI actually uses.
    """

    def __init__(self) -> None:
        self.runner_calls: list[dict[str, Any]] = []
        self.job_calls: list[dict[str, Any]] = []
        self._next_runner = {"id": "r1", "name": "stub-runner"}
        self._next_job = {"id": "j1", "name": "stub-job"}

    def setup_local_pull_schedule(self, **kwargs: Any) -> dict[str, Any]:
        self.runner_calls.append({"runner_name": kwargs["runner_name"]})
        self.job_calls.append(
            {
                "name": kwargs["job_name"],
                "cron_expression": kwargs["cron_expression"],
                "timezone": kwargs["timezone_name"],
                "local_payload": kwargs["local_payload"],
            }
        )
        return {"runner": self._next_runner, "job": self._next_job}


def test_create_daily_builds_run_batch_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The daily job must dispatch `agent.py --command run --batch`.

    Without --allow-live on the create command, the job runs the
    dry-run path. The cron expression must match SKILL.md:
    `0 6 * * 1-5` in America/New_York.
    """

    fake = _FakeClient()
    monkeypatch.setattr(setup_cron, "_build_client", lambda: fake)

    exit_code = setup_cron.main(
        [
            "create",
            "--job",
            "daily",
            "--config",
            "config.json",
            "--machine-label",
            "test-mac",
        ]
    )
    assert exit_code == 0

    job = fake.job_calls[0]
    assert job["name"].startswith("pk-lead-intelligence-daily")
    assert job["cron_expression"] == "0 6 * * 1-5"
    assert job["timezone"] == "America/New_York"
    payload = job["local_payload"]
    assert payload["skill_slug"] == "pk-lead-intelligence"
    assert payload["command"] == "run"
    assert "--batch" in payload["flags"]
    # No --allow-live without explicit opt-in.
    assert "--allow-live" not in payload["flags"]
    assert "--dry-run" in payload["flags"]

    stdout = capsys.readouterr().out
    assert '"status": "ok"' in stdout


def test_create_weekly_with_allow_live(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The weekly job must dispatch `agent.py --command weekly`.

    With --allow-live, the payload flips from --dry-run to --allow-live
    so the runner subprocesses the live share path.
    """

    fake = _FakeClient()
    monkeypatch.setattr(setup_cron, "_build_client", lambda: fake)

    exit_code = setup_cron.main(
        [
            "create",
            "--job",
            "weekly",
            "--config",
            "config.json",
            "--machine-label",
            "test-mac",
            "--allow-live",
        ]
    )
    assert exit_code == 0

    job = fake.job_calls[0]
    assert job["cron_expression"] == "0 7 * * 2"
    payload = job["local_payload"]
    assert payload["command"] == "weekly"
    assert "--allow-live" in payload["flags"]
    assert "--dry-run" not in payload["flags"]


def test_create_is_idempotent_via_client_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two creates must produce a single job_calls entry per invocation
    (the SerenCronClient handles dedup on the publisher side; here we
    just confirm the CLI does not double-fire when invoked twice).
    """

    fake = _FakeClient()
    monkeypatch.setattr(setup_cron, "_build_client", lambda: fake)

    setup_cron.main(
        ["create", "--job", "daily", "--config", "config.json", "--machine-label", "m"]
    )
    setup_cron.main(
        ["create", "--job", "daily", "--config", "config.json", "--machine-label", "m"]
    )

    # Two CLI invocations → two client calls. The SerenCronClient unit
    # tests already lock that the *second* call PUTs instead of POSTing,
    # so on the publisher side this stays at exactly one job row.
    assert len(fake.job_calls) == 2
    assert fake.job_calls[0]["name"] == fake.job_calls[1]["name"]


def test_unknown_job_kind_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--job` must be daily or weekly; anything else fails closed."""

    fake = _FakeClient()
    monkeypatch.setattr(setup_cron, "_build_client", lambda: fake)

    with pytest.raises(SystemExit):
        setup_cron.main(
            [
                "create",
                "--job",
                "hourly",
                "--config",
                "config.json",
            ]
        )

    assert fake.job_calls == []


def test_default_client_factory_builds_real_client_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke: `_build_client` returns a SerenCronClient. We don't call
    it; we just confirm the wiring is real (no AttributeError) so the
    monkeypatched tests above don't mask a broken default path.
    """

    monkeypatch.setenv("SEREN_API_KEY", "test-key")
    client = setup_cron._build_client()
    assert isinstance(client, scc.SerenCronClient)
