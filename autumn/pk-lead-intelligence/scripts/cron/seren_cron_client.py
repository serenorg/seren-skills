"""Thin client for the seren-cron publisher (Phase 5 — issue #779).

Sits on top of `scripts.seren_client.call_publisher` so we share the
existing transport, auth, and `PublisherError` surface with every
other publisher this skill calls. The transport is already covered in
`tests/test_seren_client.py`; this module is tested in
`tests/test_seren_cron_client.py` by pinning the *publisher-call shape*
through an injected `call` seam.

Surface mirrors the canonical pattern from `prophet/prophet-arb-bot/
scripts/seren_cron_client.py` so an operator who already runs another
local-pull skill sees the same subcommands here. Only the slug + the
auto-pause markers are skill-specific.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from typing import Any, Callable, Optional


PUBLISHER = "seren-cron"
SKILL_SLUG = "pk-lead-intelligence"

# 12h matches the daily cron's "miss one and self-recover" budget — the
# job fires once a day, so a 12h poll cadence guarantees we never lag
# more than half a tick. Operators can override via --poll-interval.
DEFAULT_POLL_INTERVAL_SECONDS = 12 * 60 * 60

# `call_publisher(publisher, method, path, *, body) -> Any` — the same
# signature the existing `scripts/seren_client.py` exposes.
CallPublisher = Callable[..., Any]


# --------------------------------------------------------------------- #
# Naming helpers                                                        #
# --------------------------------------------------------------------- #


def current_machine_label() -> str:
    """Human-readable machine name stored on the runner record.

    Falls back to "local-machine" rather than the literal empty string
    so a misconfigured host does not register a runner the operator
    cannot find in `list-runners` output later.
    """

    label = (os.getenv("SEREN_RUNNER_MACHINE_LABEL") or platform.node() or "").strip()
    return (label or "local-machine")[:120]


def current_platform_label() -> str:
    system = (platform.system() or "unknown").lower()
    machine = (platform.machine() or "unknown").lower().replace(" ", "-")
    return f"{system}-{machine}"


def default_runner_name(machine_label: Optional[str] = None) -> str:
    machine = (machine_label or current_machine_label()).strip() or "local-machine"
    return f"{SKILL_SLUG}-{machine}"[:120]


# --------------------------------------------------------------------- #
# Auto-pause detection (low SerenBucks → 402)                            #
# --------------------------------------------------------------------- #


# When agent.py's subprocess hits a publisher 402 (low SerenBucks),
# `scripts/seren_client.PublisherError` formats the message as
# "Publisher returned HTTP 402: …". The runner watches stdout + stderr
# for this marker and pauses the job — burning prepaid balance with
# back-to-back retries is worse than a paused job the operator can fix.
_LOW_BALANCE_MARKERS = (
    "publisher returned http 402",
    "http 402",
    '"status": 402',
    "status: 402",
)


def detect_auto_pause_reason(stdout_text: str, stderr_text: str) -> Optional[str]:
    """Return the auto-pause reason, or None if the tick is healthy."""
    haystack = f"{stdout_text or ''}\n{stderr_text or ''}".lower()
    if any(marker in haystack for marker in _LOW_BALANCE_MARKERS):
        return "low_serenbucks"
    return None


# --------------------------------------------------------------------- #
# Types                                                                  #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class PollResult:
    """Normalized response from `POST /api/runners/{id}/poll`."""

    action: str
    job: dict
    execution_result_id: str
    next_poll_seconds: int


# --------------------------------------------------------------------- #
# Client                                                                 #
# --------------------------------------------------------------------- #


def _coerce_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _coerce_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


class SerenCronClient:
    """Operations the setup CLI and the local-pull runner need.

    `call` is the `(publisher, method, path, *, body) -> Any` callable.
    Defaults to `scripts.seren_client.call_publisher`; tests inject a
    stub that records every call.
    """

    def __init__(self, *, call: Optional[CallPublisher] = None) -> None:
        if call is None:
            # Lazy import so a test that monkeypatches the module does
            # not have to set env vars just to construct the client.
            from scripts.seren_client import call_publisher

            call = call_publisher
        self._call = call

    # --- runners

    def list_runners(self) -> list[dict]:
        result = self._call(PUBLISHER, "GET", "/api/runners")
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)]
        return []

    def ensure_runner(
        self,
        *,
        runner_name: str,
        machine_label: str,
        platform_label: str,
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> dict:
        """Idempotent: GETs the runner list first; POSTs only if absent.

        The runner is the per-host identity; duplicating it splits the
        job-claim space and leaks ticks across siblings.
        """

        for runner in self.list_runners():
            if _coerce_str(runner.get("name")) == runner_name:
                return runner

        body = {
            "name": runner_name,
            "machine_label": machine_label,
            "skill_slug": SKILL_SLUG,
            "platform": platform_label,
            "poll_interval_seconds": max(5, poll_interval_seconds),
        }
        result = self._call(PUBLISHER, "POST", "/api/runners", body=body)
        if not isinstance(result, dict):
            raise RuntimeError(
                "seren-cron runner registration returned an unexpected shape"
            )
        return result

    def delete_runner(self, runner_id: str) -> Any:
        return self._call(PUBLISHER, "DELETE", f"/api/runners/{runner_id}")

    # --- jobs

    def list_jobs(self) -> list[dict]:
        result = self._call(PUBLISHER, "GET", "/api/jobs")
        if isinstance(result, list):
            return [j for j in result if isinstance(j, dict)]
        return []

    def upsert_local_pull_job(
        self,
        *,
        name: str,
        runner_id: str,
        cron_expression: str,
        timezone_name: str,
        local_payload: dict,
    ) -> dict:
        """Create-or-update so re-running `setup_cron create` does not
        accumulate duplicate jobs.

        We list-then-PUT for an existing match; otherwise POST. This is
        the pattern peer skills use; the publisher honors PUT to swap
        cron expression and payload in place.
        """

        body = {
            "name": name,
            "cron_expression": cron_expression,
            "timezone": timezone_name,
            "execution_mode": "local_pull",
            "runner_id": runner_id,
            "local_payload": local_payload,
        }
        existing = next(
            (
                j
                for j in self.list_jobs()
                if _coerce_str(j.get("name")) == name and _coerce_str(j.get("id"))
            ),
            None,
        )
        if existing is None:
            result = self._call(PUBLISHER, "POST", "/api/jobs", body=body)
        else:
            job_id = _coerce_str(existing.get("id"))
            result = self._call(PUBLISHER, "PUT", f"/api/jobs/{job_id}", body=body)
        if not isinstance(result, dict):
            raise RuntimeError(
                "seren-cron job upsert returned an unexpected shape"
            )
        return result

    def setup_local_pull_schedule(
        self,
        *,
        runner_name: str,
        machine_label: str,
        platform_label: str,
        poll_interval_seconds: int,
        job_name: str,
        cron_expression: str,
        timezone_name: str,
        local_payload: dict,
    ) -> dict:
        """One-shot: ensure runner + upsert job. Returns both records."""

        runner = self.ensure_runner(
            runner_name=runner_name,
            machine_label=machine_label,
            platform_label=platform_label,
            poll_interval_seconds=poll_interval_seconds,
        )
        job = self.upsert_local_pull_job(
            name=job_name,
            runner_id=_coerce_str(runner.get("id")),
            cron_expression=cron_expression,
            timezone_name=timezone_name,
            local_payload=local_payload,
        )
        return {"runner": runner, "job": job}

    def pause_job(self, job_id: str) -> Any:
        return self._call(PUBLISHER, "POST", f"/api/jobs/{job_id}/pause", body={})

    def resume_job(self, job_id: str) -> Any:
        return self._call(PUBLISHER, "POST", f"/api/jobs/{job_id}/resume", body={})

    def delete_job(self, job_id: str) -> Any:
        return self._call(PUBLISHER, "DELETE", f"/api/jobs/{job_id}")

    # --- polling

    def poll(
        self, runner_id: str, *, last_seen_result_id: Optional[str] = None
    ) -> PollResult:
        body = {
            "last_seen_result_id": last_seen_result_id,
            "supports": {
                "stdout_tail": True,
                "stderr_tail": True,
                "response_body": True,
            },
        }
        data = self._call(
            PUBLISHER, "POST", f"/api/runners/{runner_id}/poll", body=body
        )
        if not isinstance(data, dict):
            data = {}
        job = data.get("job") if isinstance(data.get("job"), dict) else {}
        exec_result = (
            data.get("execution_result")
            if isinstance(data.get("execution_result"), dict)
            else {}
        )
        return PollResult(
            action=_coerce_str(data.get("action"), "idle"),
            job=job,
            execution_result_id=_coerce_str(exec_result.get("id")),
            next_poll_seconds=_coerce_int(
                data.get("next_poll_seconds"), DEFAULT_POLL_INTERVAL_SECONDS
            ),
        )

    def submit_result(
        self,
        runner_id: str,
        *,
        execution_result_id: str,
        status: str,
        response_body: str,
        exit_code: int,
        stdout_tail: str,
        stderr_tail: str,
    ) -> Any:
        body = {
            "execution_result_id": execution_result_id,
            "status": status,
            "response_body": response_body,
            "exit_code": exit_code,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        }
        return self._call(
            PUBLISHER, "POST", f"/api/runners/{runner_id}/results", body=body
        )
