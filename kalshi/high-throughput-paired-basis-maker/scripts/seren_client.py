#!/usr/bin/env python3
"""Seren publisher gateway client for Kalshi basis maker."""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SEREN_API_BASE = "https://api.serendb.com"
DEFAULT_TIMEOUT = 30
SEREN_CRON_PUBLISHER = "seren-cron"
DEFAULT_POLL_INTERVAL_SECONDS = 30


def _get_api_key() -> str:
    return os.getenv("API_KEY", "").strip() or os.getenv("SEREN_API_KEY", "").strip()


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def call_publisher_json(
    *,
    publisher: str,
    method: str = "GET",
    path: str = "/",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT,
) -> Any:
    """Call a Seren publisher endpoint and return parsed JSON."""
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("Missing SEREN_API_KEY or API_KEY for publisher call")

    url = f"{SEREN_API_BASE}/publishers/{publisher}{path}"
    all_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if headers:
        all_headers.update(headers)

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, headers=all_headers, method=method)
    with urlopen(req, timeout=timeout_seconds) as resp:
        raw = resp.read().decode("utf-8")
        if not raw:
            return {}
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "body" in parsed and "status" in parsed:
            return parsed["body"]
        return parsed


def current_machine_label() -> str:
    return socket.gethostname()


def default_local_pull_runner_name(skill_slug: str, machine_label: str = "") -> str:
    label = machine_label or current_machine_label()
    return f"{skill_slug}-{label}"


# ---------------------------------------------------------------------------
# seren-cron helpers
# ---------------------------------------------------------------------------

def list_seren_cron_jobs(timeout_seconds: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    result = call_publisher_json(
        publisher=SEREN_CRON_PUBLISHER,
        method="GET",
        path="/api/jobs",
        timeout_seconds=timeout_seconds,
    )
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, list):
            return data
    if isinstance(result, list):
        return result
    return []


def list_seren_cron_runners(timeout_seconds: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    result = call_publisher_json(
        publisher=SEREN_CRON_PUBLISHER,
        method="GET",
        path="/api/runners",
        timeout_seconds=timeout_seconds,
    )
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, list):
            return data
    if isinstance(result, list):
        return result
    return []


def ensure_local_pull_runner(
    *,
    skill_slug: str,
    runner_name: str,
    machine_label: str,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    runners = list_seren_cron_runners(timeout_seconds=timeout_seconds)
    for runner in runners:
        if _safe_str(runner.get("name"), "") == runner_name:
            return runner

    result = call_publisher_json(
        publisher=SEREN_CRON_PUBLISHER,
        method="POST",
        path="/api/runners",
        body={
            "name": runner_name,
            "machine_label": machine_label,
            "skill_slug": skill_slug,
            "poll_interval_seconds": poll_interval_seconds,
        },
        timeout_seconds=timeout_seconds,
    )
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result if isinstance(result, dict) else {}


def setup_local_pull_schedule(
    *,
    skill_slug: str,
    runner_name: str,
    machine_label: str,
    poll_interval_seconds: int,
    job_name: str,
    cron_expression: str,
    timezone_name: str = "UTC",
    config_path: str = "config.json",
    run_type: str = "trade",
    yes_live: bool = False,
    timeout_seconds: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    runner = ensure_local_pull_runner(
        skill_slug=skill_slug,
        runner_name=runner_name,
        machine_label=machine_label,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
    )
    runner_id = _safe_str(runner.get("id"), "")

    jobs = list_seren_cron_jobs(timeout_seconds=timeout_seconds)
    for job in jobs:
        if _safe_str(job.get("name"), "") == job_name:
            return {"runner": runner, "job": job}

    job_result = call_publisher_json(
        publisher=SEREN_CRON_PUBLISHER,
        method="POST",
        path="/api/jobs",
        body={
            "name": job_name,
            "cron_expression": cron_expression,
            "timezone": timezone_name,
            "execution_mode": "local_pull",
            "runner_id": runner_id,
            "local_payload": {
                "skill_slug": skill_slug,
                "config_path": config_path,
                "run_type": run_type,
                "yes_live": yes_live,
            },
        },
        timeout_seconds=timeout_seconds,
    )
    job = job_result if isinstance(job_result, dict) else {}
    if "data" in job:
        job = job["data"]
    return {"runner": runner, "job": job}


def poll_local_pull_runner(
    runner_id: str,
    *,
    last_seen_result_id: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    params: dict[str, str] = {}
    if last_seen_result_id:
        params["last_seen_result_id"] = last_seen_result_id
    path = f"/api/runners/{runner_id}/poll"
    if params:
        path = f"{path}?{urlencode(params)}"
    result = call_publisher_json(
        publisher=SEREN_CRON_PUBLISHER,
        method="GET",
        path=path,
        timeout_seconds=timeout_seconds,
    )
    return result if isinstance(result, dict) else {}


def submit_local_pull_result(
    runner_id: str,
    *,
    execution_result_id: str,
    status: str,
    response_body: str,
    exit_code: int,
    stdout_tail: str = "",
    stderr_tail: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    result = call_publisher_json(
        publisher=SEREN_CRON_PUBLISHER,
        method="POST",
        path=f"/api/runners/{runner_id}/results/{execution_result_id}",
        body={
            "status": status,
            "response_body": response_body,
            "exit_code": exit_code,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        },
        timeout_seconds=timeout_seconds,
    )
    return result if isinstance(result, dict) else {}


def check_serenbucks_balance(api_key: str | None = None) -> float:
    """Return SerenBucks funded balance in USD."""
    key = api_key or _get_api_key()
    if not key:
        return -1.0
    req = Request(
        f"{SEREN_API_BASE}/wallet/balance",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                bal = data.get("data", {})
                if isinstance(bal, dict):
                    raw = _safe_str(bal.get("funded_balance_usd"), "0")
                    return float(raw.replace("$", "").replace(",", ""))
            return 0.0
    except Exception:
        return -1.0
