#!/usr/bin/env python3
"""
Create/update seren-cron jobs for saas-short-trader.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List

import requests


class CronSetup:
    def __init__(self, api_key: str, gateway_url: str = "https://api.serendb.com"):
        self.base = f"{gateway_url.rstrip('/')}/publishers/seren-cron"
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )

    def call(self, method: str, path: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
        url = f"{self.base}{path}"
        kwargs: Dict[str, Any] = {"timeout": 60}
        if body is not None:
            kwargs["json"] = body
        resp = self.session.request(method.upper(), url, **kwargs)
        text = resp.text or ""
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {path} failed: {resp.status_code} {text}")
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"body": text}

    @staticmethod
    def extract_jobs(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        body = payload.get("body", payload)
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            return body["data"]
        if isinstance(payload.get("data"), list):
            return payload["data"]
        return []

    def upsert_jobs(self, jobs: List[Dict[str, Any]], dry_run: bool = False) -> List[Dict[str, Any]]:
        existing_payload = self.call("GET", "/api/v1/jobs")
        existing = {j.get("name"): j for j in self.extract_jobs(existing_payload)}
        results = []
        for job in jobs:
            old = existing.get(job["name"])
            if dry_run:
                results.append({"name": job["name"], "operation": "update" if old else "create"})
                continue
            if old and old.get("id"):
                resp = self.call("PUT", f"/api/v1/jobs/{old['id']}", body=job)
                results.append({"name": job["name"], "operation": "update", "response": resp})
            else:
                resp = self.call("POST", "/api/v1/jobs", body=job)
                results.append({"name": job["name"], "operation": "create", "response": resp})
        return results


def build_jobs(runner_url: str, webhook_secret: str, timezone: str, mode: str, learning_mode: str) -> List[Dict[str, Any]]:
    run_url = f"{runner_url.rstrip('/')}/run"
    headers = {"Content-Type": "application/json", "x-webhook-secret": webhook_secret}
    common = {
        "skill": "saas-short-trader",
        "run_profile": "continuous",
        "mode": mode,
        "learning_mode": learning_mode,
        "max_names_scored": 30,
        "max_names_orders": 8,
        "min_conviction": 65,
        "strict_required_feeds": True,
        "persist_to_serendb": True,
    }
    return [
        {
            "name": "saas-short-trader-scan",
            "schedule": "15 8 * * 1-5",
            "timezone": timezone,
            "url": run_url,
            "method": "POST",
            "headers": headers,
            "body": {**common, "run_type": "scan"},
        },
        {
            "name": "saas-short-trader-monitor",
            "schedule": "15 10-15 * * 1-5",
            "timezone": timezone,
            "url": run_url,
            "method": "POST",
            "headers": headers,
            "body": {**common, "run_type": "monitor"},
        },
        {
            "name": "saas-short-trader-post-close",
            "schedule": "20 16 * * 1-5",
            "timezone": timezone,
            "url": run_url,
            "method": "POST",
            "headers": headers,
            "body": {**common, "run_type": "post-close"},
        },
        {
            "name": "saas-short-trader-label-update",
            "schedule": "35 16 * * 1-5",
            "timezone": timezone,
            "url": run_url,
            "method": "POST",
            "headers": headers,
            "body": {"action": "label-update", "mode": mode},
        },
        {
            "name": "saas-short-trader-retrain",
            "schedule": "30 9 * * 6",
            "timezone": timezone,
            "url": run_url,
            "method": "POST",
            "headers": headers,
            "body": {"action": "retrain", "mode": mode},
        },
        {
            "name": "saas-short-trader-promotion-check",
            "schedule": "0 7 * * 1",
            "timezone": timezone,
            "url": run_url,
            "method": "POST",
            "headers": headers,
            "body": {"action": "promotion-check", "mode": mode},
        },
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Setup seren-cron jobs for saas-short-trader")
    parser.add_argument("--runner-url", required=True, help="Public runner URL, e.g. https://bot.example.com")
    parser.add_argument("--webhook-secret", required=True, help="x-webhook-secret value")
    parser.add_argument("--api-key", default=os.getenv("SEREN_API_KEY", ""), help="SEREN_API_KEY")
    parser.add_argument("--gateway-url", default=os.getenv("SEREN_GATEWAY_URL", "https://api.serendb.com"))
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--mode", default="paper-sim", choices=["paper", "paper-sim", "live"])
    parser.add_argument("--learning-mode", default="adaptive-paper")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("SEREN_API_KEY is required (--api-key or env)")

    jobs = build_jobs(
        runner_url=args.runner_url,
        webhook_secret=args.webhook_secret,
        timezone=args.timezone,
        mode=args.mode,
        learning_mode=args.learning_mode,
    )
    setup = CronSetup(api_key=args.api_key, gateway_url=args.gateway_url)
    results = setup.upsert_jobs(jobs=jobs, dry_run=args.dry_run)
    print(json.dumps(results, indent=2))

    if not args.dry_run:
        listed = setup.call("GET", "/api/v1/jobs")
        jobs = [j for j in setup.extract_jobs(listed) if str(j.get("name", "")).startswith("saas-short-trader-")]
        jobs.sort(key=lambda x: x.get("name", ""))
        print("\nActive jobs:")
        for j in jobs:
            print(
                f"- {j.get('name')} | id={j.get('id')} | cron={j.get('cron_expression')} "
                f"| tz={j.get('timezone')} | enabled={j.get('enabled')} | next={j.get('next_run_time')}"
            )


if __name__ == "__main__":
    main()
