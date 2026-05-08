"""SerenCronClient — thin wrapper around the seren-cron publisher.

Plan §18 (Phase 12). Mirrors the BountyClient shape — every operation
goes through a `gateway.call(publisher, method, path, body, headers)`
seam so tests can substitute a StubGateway. Adapted from
polymarket-bot/scripts/polymarket_live.py per plan §18.1; copied
rather than imported because the source module is 1500+ lines of
unrelated polymarket plumbing.

Responsibilities:
  - register / fetch the local-pull runner for this skill
  - upsert the local-pull job carrying the user's saved inputs
  - poll the runner and submit per-tick results
  - pause / resume / delete jobs and runners
  - detect auto-pause signals (pool exhausted, low SerenBucks)

The runner uses `detect_auto_pause_reason` to decide whether to call
`/jobs/{id}/pause` after the agent.py subprocess exits — see
plan §18.3.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


def _ssl_context() -> ssl.SSLContext:
    """Return an SSL context that uses certifi when available.

    Python's bundled OpenSSL on macOS does not always pick up the
    system CA bundle, so `urlopen` fails with CERTIFICATE_VERIFY_FAILED
    against api.serendb.com. Falling back to the platform default keeps
    Linux/CI working when certifi is not installed.
    """
    try:
        import certifi  # type: ignore[import-not-found]

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

PUBLISHER = "seren-cron"
SKILL_SLUG = "prophet-bounty-runner"
DEFAULT_POLL_INTERVAL_SECONDS = 30
DEFAULT_TIMEOUT_SECONDS = 30.0

# Plan §18.3: cron schedule defaults to every 6 hours, on the hour, UTC.
# Aligned with the 1h Privy JWT (each tick re-OTPs) without flooding the
# user's inbox (4 OTP emails/day max).
DEFAULT_CRON_EXPRESSION = "0 */6 * * *"
DEFAULT_TIMEZONE_NAME = "UTC"

# Auto-pause signals the runner watches for in agent.py output.
# Plan §18.3 lists three: blocked_no_bounty (pool exhausted) and
# publisher 402 (low SerenBucks); blocked_otp does not auto-pause.
_AUTO_PAUSE_REASONS = {
    "blocked_no_bounty": "pool_exhausted",
    "blocked_low_serenbucks": "low_serenbucks",
}
_LOW_SERENBUCKS_MARKERS = ("status: 402", "status:402", '"status":402', "402 ")


@dataclass
class PollResult:
    """Normalized seren-cron poll response."""

    action: str
    job: dict[str, Any]
    execution_result_id: str
    next_poll_seconds: int


# ---------------------------------------------------------------------------
# Naming + identity helpers


def current_machine_label() -> str:
    label = (os.getenv("SEREN_RUNNER_MACHINE_LABEL") or platform.node() or "").strip()
    return label[:120] or "local-machine"


def current_platform_label() -> str:
    system = (platform.system() or "unknown").lower()
    machine = (platform.machine() or "unknown").lower().replace(" ", "-")
    return f"{system}-{machine}"


def default_runner_name(machine_label: str | None = None) -> str:
    machine = (machine_label or current_machine_label()).strip() or "local-machine"
    return f"{SKILL_SLUG}-{machine}"[:120]


def default_user_id_short(api_key: str | None = None) -> str:
    """Stable 8-char id derived from SEREN_API_KEY.

    Plan §18.3 wants `prophet-bounty-runner-{user_id_short}` so two Seren
    accounts on one machine don't collide on a single job. We can't query
    /auth/me before the cron is set up (auth happens inside agent.py), so
    we hash the bearer token client-side. Stable per-key, no PII.
    """
    raw = (api_key if api_key is not None else os.getenv("SEREN_API_KEY") or "").strip()
    if not raw:
        return "default"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def default_job_name(user_id_short: str | None = None) -> str:
    suffix = (user_id_short or default_user_id_short()).strip() or "default"
    return f"{SKILL_SLUG}-{suffix}"[:120]


# ---------------------------------------------------------------------------
# Auto-pause detection


def detect_auto_pause_reason(stdout_text: str, stderr_text: str) -> str | None:
    """Return the auto-pause reason, or None if the tick is healthy.

    Plan §18.3 auto-pause triggers:
      - agent.py reports `reason=blocked_no_bounty` → "pool_exhausted"
      - publisher returns 402 (low SerenBucks) → "low_serenbucks"
    blocked_otp does NOT auto-pause; transient OTP delivery lag should
    self-heal on the next tick.
    """
    stdout_stripped = (stdout_text or "").strip()
    if stdout_stripped:
        try:
            parsed = json.loads(stdout_stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            reason = (parsed.get("reason") or "").strip()
            mapped = _AUTO_PAUSE_REASONS.get(reason)
            if mapped is not None:
                return mapped

    haystack = f"{stdout_text or ''}\n{stderr_text or ''}".lower()
    if any(marker in haystack for marker in _LOW_SERENBUCKS_MARKERS):
        return "low_serenbucks"
    return None


# ---------------------------------------------------------------------------
# Production gateway (urllib-based; tests pass a stub)


class HttpGateway:
    """Default gateway used by setup_cron.py / run_local_pull_runner.py.

    Mirrors the (publisher, method, path, body, headers) seam that the
    test StubGateway implements, so swapping the two requires no code
    change in SerenCronClient. Reads SEREN_API_KEY from the environment;
    desktop-injected `API_KEY` is also accepted as a fallback.
    """

    SEREN_API_BASE = "https://api.serendb.com"
    PUBLISHERS_PREFIX = "/publishers/"

    def __init__(self, *, api_key: str | None = None, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.api_key = (api_key or os.getenv("SEREN_API_KEY") or os.getenv("API_KEY") or "").strip()
        self.timeout_seconds = timeout_seconds

    def call(
        self,
        publisher: str,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if not self.api_key:
            raise RuntimeError("SEREN_API_KEY (or runtime-injected API_KEY) is required for HttpGateway calls.")
        req_headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}
        if headers:
            req_headers.update(headers)
        data = None
        if body is not None:
            req_headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.SEREN_API_BASE}{self.PUBLISHERS_PREFIX}{publisher}{path}",
            headers=req_headers,
            method=method.upper(),
            data=data,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds, context=_ssl_context()) as response:
                text = response.read().decode("utf-8")
        except Exception as exc:
            # Surface the upstream error body when a publisher returns a
            # structured GraphQL error or a JSON `{error: ...}` envelope.
            # Phase-14 diagnostic: bare "HTTP Error 401: Unauthorized"
            # hides the prophet-ai message that would tell us why a JWT
            # is rejected (e.g. "user not registered, call
            # registerWithPrivy"). Re-raise with the body appended.
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8")  # type: ignore[attr-defined]
            except Exception:
                body_text = ""
            if body_text:
                raise RuntimeError(f"{exc} :: body={body_text[:600]}") from exc
            raise
        if not text:
            return {}
        parsed = json.loads(text)
        # Live probe (Phase 14): every Seren publisher response wraps the
        # actual payload in `{data: {status, body, response_bytes, cost,
        # ...metadata}}`. Unwrap to `body` so callers see the publisher's
        # native shape (e.g. seren-bounty's `{bounties: [...]}` or prophet-ai's
        # GraphQL `{data, errors}`). Test stubs register the unwrapped shape
        # already, so this matches both production and StubGateway.
        if isinstance(parsed, dict) and "data" in parsed and isinstance(parsed["data"], dict):
            inner = parsed["data"]
            if "body" in inner:
                return inner["body"]
            return inner
        return parsed


# ---------------------------------------------------------------------------
# Client


def _safe_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _safe_int(value: Any, default: int) -> int:
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


def _extract(payload: Any) -> Any:
    """Unwrap publisher response envelopes. seren-cron returns either the
    raw object or `{"data": {...}}` / `{"body": {...}}` depending on path."""
    if isinstance(payload, dict):
        if "body" in payload:
            return _extract(payload["body"])
        if payload.get("success") is False:
            raise RuntimeError(_safe_str(payload.get("error"), "Publisher request failed."))
        if "data" in payload:
            return payload["data"]
    return payload


class SerenCronClient:
    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway

    # --- runners

    def list_runners(self) -> list[dict[str, Any]]:
        rows = _extract(self.gateway.call(PUBLISHER, "GET", "/api/runners"))
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def ensure_runner(
        self,
        *,
        runner_name: str,
        machine_label: str,
        platform_label: str,
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> dict[str, Any]:
        for runner in self.list_runners():
            if _safe_str(runner.get("name")) == runner_name:
                return runner
        body = {
            "name": runner_name,
            "machine_label": machine_label,
            "skill_slug": SKILL_SLUG,
            "platform": platform_label,
            "poll_interval_seconds": max(5, poll_interval_seconds),
        }
        runner = _extract(self.gateway.call(PUBLISHER, "POST", "/api/runners", body=body))
        if not isinstance(runner, dict):
            raise RuntimeError("seren-cron runner registration did not return a runner object.")
        return runner

    def delete_runner(self, runner_id: str) -> Any:
        return _extract(self.gateway.call(PUBLISHER, "DELETE", f"/api/runners/{runner_id}"))

    # --- jobs

    def list_jobs(self) -> list[dict[str, Any]]:
        rows = _extract(self.gateway.call(PUBLISHER, "GET", "/api/jobs"))
        return [j for j in rows if isinstance(j, dict)] if isinstance(rows, list) else []

    def upsert_local_pull_job(
        self,
        *,
        name: str,
        runner_id: str,
        cron_expression: str,
        timezone_name: str,
        local_payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = {
            "name": name,
            "cron_expression": cron_expression,
            "timezone": timezone_name,
            "execution_mode": "local_pull",
            "runner_id": runner_id,
            "local_payload": local_payload,
        }
        existing = next(
            (j for j in self.list_jobs() if _safe_str(j.get("name")) == name and _safe_str(j.get("id"))),
            None,
        )
        if existing is None:
            payload = self.gateway.call(PUBLISHER, "POST", "/api/jobs", body=body)
        else:
            payload = self.gateway.call(
                PUBLISHER, "PUT", f"/api/jobs/{_safe_str(existing.get('id'))}", body=body
            )
        job = _extract(payload)
        if not isinstance(job, dict):
            raise RuntimeError("seren-cron job upsert did not return a job object.")
        return job

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
        local_payload: dict[str, Any],
    ) -> dict[str, Any]:
        runner = self.ensure_runner(
            runner_name=runner_name,
            machine_label=machine_label,
            platform_label=platform_label,
            poll_interval_seconds=poll_interval_seconds,
        )
        job = self.upsert_local_pull_job(
            name=job_name,
            runner_id=_safe_str(runner.get("id")),
            cron_expression=cron_expression,
            timezone_name=timezone_name,
            local_payload=local_payload,
        )
        return {"runner": runner, "job": job}

    def pause_job(self, job_id: str) -> Any:
        return _extract(self.gateway.call(PUBLISHER, "POST", f"/api/jobs/{job_id}/pause", body={}))

    def resume_job(self, job_id: str) -> Any:
        return _extract(self.gateway.call(PUBLISHER, "POST", f"/api/jobs/{job_id}/resume", body={}))

    def delete_job(self, job_id: str) -> Any:
        return _extract(self.gateway.call(PUBLISHER, "DELETE", f"/api/jobs/{job_id}"))

    # --- polling

    def poll(self, runner_id: str, *, last_seen_result_id: str | None = None) -> PollResult:
        body = {
            "last_seen_result_id": last_seen_result_id,
            "supports": {"stdout_tail": True, "stderr_tail": True, "response_body": True},
        }
        data = _extract(self.gateway.call(PUBLISHER, "POST", f"/api/runners/{runner_id}/poll", body=body))
        if not isinstance(data, dict):
            data = {}
        execution_result = data.get("execution_result")
        if not isinstance(execution_result, dict):
            execution_result = {}
        job = data.get("job")
        if not isinstance(job, dict):
            job = {}
        return PollResult(
            action=_safe_str(data.get("action"), "idle"),
            job=job,
            execution_result_id=_safe_str(execution_result.get("id")),
            next_poll_seconds=_safe_int(data.get("next_poll_seconds"), DEFAULT_POLL_INTERVAL_SECONDS),
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
        return _extract(
            self.gateway.call(PUBLISHER, "POST", f"/api/runners/{runner_id}/results", body=body)
        )
