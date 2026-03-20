#!/usr/bin/env python3
"""Create and manage seren-cron jobs for kraken-grid-trader."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import requests


DEFAULT_GATEWAY_URL = "https://api.serendb.com"
JOB_PREFIX = "kraken-grid-trader-"


class CronSetup:
    def __init__(self, api_key: str, gateway_url: str = DEFAULT_GATEWAY_URL):
        self.base = f"{gateway_url.rstrip('/')}/publishers/seren-cron"
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base}{path}"
        kwargs: dict[str, Any] = {"timeout": 60}
        if body is not None:
            kwargs["json"] = body
        response = self.session.request(method.upper(), url, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} failed: {response.status_code} {response.text[:200]}")
        payload = response.json()
        if isinstance(payload, dict) and "body" in payload:
            return payload["body"]
        return payload

    @staticmethod
    def extract_jobs(payload: dict[str, Any]) -> list[dict[str, Any]]:
        body = payload.get("body", payload)
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            return body["data"]
        if isinstance(payload.get("data"), list):
            return payload["data"]
        return []

    def upsert_jobs(self, jobs: list[dict[str, Any]], dry_run: bool = False) -> list[dict[str, Any]]:
        existing_payload = self.call("GET", "/api/v1/jobs")
        existing = {job.get("name"): job for job in self.extract_jobs(existing_payload)}
        results = []
        for job in jobs:
            prior = existing.get(job["name"])
            if dry_run:
                results.append({"name": job["name"], "operation": "update" if prior else "create"})
                continue
            if prior and prior.get("id"):
                response = self.call("PUT", f"/api/v1/jobs/{prior['id']}", body=job)
                results.append({"name": job["name"], "operation": "update", "response": response})
            else:
                response = self.call("POST", "/api/v1/jobs", body=job)
                results.append({"name": job["name"], "operation": "create", "response": response})
        return results


def build_jobs(
    *,
    runner_url: str,
    webhook_secret: str,
    timezone: str,
    cycle_schedule: str,
    review_schedule: str,
    safety_schedule: str,
) -> list[dict[str, Any]]:
    base_url = runner_url.rstrip("/")
    headers = {"Content-Type": "application/json", "X-Webhook-Secret": webhook_secret}
    return [
        {
            "name": f"{JOB_PREFIX}cycle",
            "schedule": cycle_schedule,
            "timezone": timezone,
            "url": f"{base_url}/run",
            "method": "POST",
            "headers": headers,
            "body": {"action": "cycle"},
        },
        {
            "name": f"{JOB_PREFIX}safety-check",
            "schedule": safety_schedule,
            "timezone": timezone,
            "url": f"{base_url}/safety-check",
            "method": "POST",
            "headers": headers,
            "body": {"action": "safety-check"},
        },
        {
            "name": f"{JOB_PREFIX}weekly-review",
            "schedule": review_schedule,
            "timezone": timezone,
            "url": f"{base_url}/review",
            "method": "POST",
            "headers": headers,
            "body": {"action": "review"},
        },
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage seren-cron jobs for kraken-grid-trader")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--runner-url", required=True, help="Public runner URL, e.g. https://bot.example.com")
    create.add_argument("--webhook-secret", required=True, help="X-Webhook-Secret value")
    create.add_argument("--api-key", default=os.getenv("SEREN_API_KEY", ""), help="SEREN_API_KEY")
    create.add_argument("--gateway-url", default=os.getenv("SEREN_GATEWAY_URL", DEFAULT_GATEWAY_URL))
    create.add_argument("--timezone", default="America/New_York")
    create.add_argument("--cycle-schedule", default="*/5 * * * *")
    create.add_argument("--safety-schedule", default="*/30 * * * *")
    create.add_argument("--review-schedule", default="0 7 * * 1")
    create.add_argument("--dry-run", action="store_true")

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--api-key", default=os.getenv("SEREN_API_KEY", ""), help="SEREN_API_KEY")
    list_parser.add_argument("--gateway-url", default=os.getenv("SEREN_GATEWAY_URL", DEFAULT_GATEWAY_URL))

    for command in ("pause", "resume", "delete"):
        action = sub.add_parser(command)
        action.add_argument("--job-id", required=True)
        action.add_argument("--api-key", default=os.getenv("SEREN_API_KEY", ""), help="SEREN_API_KEY")
        action.add_argument("--gateway-url", default=os.getenv("SEREN_GATEWAY_URL", DEFAULT_GATEWAY_URL))

    return parser.parse_args()


def _build_setup(api_key: str, gateway_url: str) -> CronSetup:
    if not api_key:
        raise SystemExit("SEREN_API_KEY is required (--api-key or env)")
    return CronSetup(api_key=api_key, gateway_url=gateway_url)


def main() -> None:
    args = parse_args()
    setup = _build_setup(args.api_key, args.gateway_url)

    if args.command == "create":
        jobs = build_jobs(
            runner_url=args.runner_url,
            webhook_secret=args.webhook_secret,
            timezone=args.timezone,
            cycle_schedule=args.cycle_schedule,
            review_schedule=args.review_schedule,
            safety_schedule=args.safety_schedule,
        )
        results = setup.upsert_jobs(jobs=jobs, dry_run=args.dry_run)
        print(json.dumps(results, indent=2))
        if args.dry_run:
            return
        listed = setup.call("GET", "/api/v1/jobs")
        jobs = [
            job for job in setup.extract_jobs(listed)
            if str(job.get("name", "")).startswith(JOB_PREFIX)
        ]
        jobs.sort(key=lambda item: item.get("name", ""))
        print("\nActive jobs:")
        for job in jobs:
            print(
                f"- {job.get('name')} | id={job.get('id')} | cron={job.get('cron_expression')} "
                f"| tz={job.get('timezone')} | enabled={job.get('enabled')} | next={job.get('next_run_time')}"
            )
        return

    if args.command == "list":
        payload = setup.call("GET", "/api/v1/jobs")
        jobs = [
            job for job in setup.extract_jobs(payload)
            if str(job.get("name", "")).startswith(JOB_PREFIX)
        ]
        jobs.sort(key=lambda item: item.get("name", ""))
        print(json.dumps(jobs, indent=2))
        return

    if args.command == "pause":
        print(json.dumps(setup.call("POST", f"/api/v1/jobs/{args.job_id}/pause"), indent=2))
        return

    if args.command == "resume":
        print(json.dumps(setup.call("POST", f"/api/v1/jobs/{args.job_id}/resume"), indent=2))
        return

    if args.command == "delete":
        print(json.dumps(setup.call("DELETE", f"/api/v1/jobs/{args.job_id}"), indent=2))
        return


if __name__ == "__main__":
    main()
