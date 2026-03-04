#!/usr/bin/env python3
"""Create/manage seren-cron schedules for Kraken Smart DCA Bot."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


DEFAULT_API_BASE = "https://api.serendb.com"


class CronError(RuntimeError):
    """Raised when seren-cron API interactions fail."""


class SerenCronClient:
    def __init__(self, *, api_key: str, api_base_url: str = DEFAULT_API_BASE) -> None:
        if requests is None:
            raise CronError("requests dependency is required for setup_cron.py")
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.api_base_url}/publishers/seren-cron{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = requests.request(method, url, headers=headers, json=body or {}, timeout=30)
        if response.status_code >= 400:
            raise CronError(f"seren-cron API error: status={response.status_code} body={response.text[:200]}")
        payload = response.json()
        if isinstance(payload, dict) and "body" in payload:
            return payload["body"]
        return payload

    def create_job(self, *, name: str, schedule: str, url: str, method: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/jobs",
            {
                "name": name,
                "schedule": schedule,
                "url": url,
                "method": method,
            },
        )

    def list_jobs(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/jobs")

    def pause_job(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/jobs/{job_id}/pause")

    def resume_job(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/jobs/{job_id}/resume")

    def delete_job(self, job_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/api/v1/jobs/{job_id}")


def _build_client() -> SerenCronClient:
    api_key = (os.getenv("SEREN_API_KEY") or "").strip()
    if not api_key:
        raise CronError("SEREN_API_KEY is required")
    base_url = os.getenv("SEREN_API_BASE_URL", DEFAULT_API_BASE)
    return SerenCronClient(api_key=api_key, api_base_url=base_url)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage seren-cron jobs for smart-dca-bot")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--url", required=True, help="Trigger URL, e.g. http://localhost:8787/run")
    create.add_argument("--schedule", default="*/15 * * * *")
    create.add_argument("--name", default="kraken-smart-dca-bot")
    create.add_argument("--method", default="POST")

    sub.add_parser("list")

    pause = sub.add_parser("pause")
    pause.add_argument("--job-id", required=True)

    resume = sub.add_parser("resume")
    resume.add_argument("--job-id", required=True)

    delete = sub.add_parser("delete")
    delete.add_argument("--job-id", required=True)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        client = _build_client()
        if args.command == "create":
            out = client.create_job(
                name=args.name,
                schedule=args.schedule,
                url=args.url,
                method=args.method.upper(),
            )
        elif args.command == "list":
            out = client.list_jobs()
        elif args.command == "pause":
            out = client.pause_job(args.job_id)
        elif args.command == "resume":
            out = client.resume_job(args.job_id)
        elif args.command == "delete":
            out = client.delete_job(args.job_id)
        else:  # pragma: no cover
            raise CronError(f"unknown command {args.command}")
    except CronError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1

    print(json.dumps({"status": "ok", "result": out}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
