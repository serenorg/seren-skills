#!/usr/bin/env python3
"""Create/manage seren-cron jobs for the Curve Gauge Yield Trader."""

from __future__ import annotations

import argparse
import json
import os
import sys

from agent import DEFAULT_API_BASE, PublisherError, SerenPublisherClient


def _build_client() -> SerenPublisherClient:
    api_key = os.environ.get("SEREN_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SEREN_API_KEY is required in the environment.")
    base_url = os.environ.get("SEREN_API_BASE_URL", DEFAULT_API_BASE)
    return SerenPublisherClient(api_key=api_key, base_url=base_url)


def create_job(
    client: SerenPublisherClient,
    *,
    name: str,
    schedule: str,
    url: str,
    method: str,
) -> dict:
    return client.call(
        publisher="seren-cron",
        method="POST",
        path="/api/v1/jobs",
        body={
            "name": name,
            "schedule": schedule,
            "url": url,
            "method": method,
        },
    )


def list_jobs(client: SerenPublisherClient) -> dict:
    return client.call(
        publisher="seren-cron",
        method="GET",
        path="/api/v1/jobs",
        body={},
    )


def pause_job(client: SerenPublisherClient, job_id: str) -> dict:
    return client.call(
        publisher="seren-cron",
        method="POST",
        path=f"/api/v1/jobs/{job_id}/pause",
        body={},
    )


def resume_job(client: SerenPublisherClient, job_id: str) -> dict:
    return client.call(
        publisher="seren-cron",
        method="POST",
        path=f"/api/v1/jobs/{job_id}/resume",
        body={},
    )


def delete_job(client: SerenPublisherClient, job_id: str) -> dict:
    return client.call(
        publisher="seren-cron",
        method="DELETE",
        path=f"/api/v1/jobs/{job_id}",
        body={},
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage seren-cron jobs for curve-gauge-yield-trader."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a cron job.")
    create.add_argument("--url", required=True, help="Trigger URL, e.g. http://localhost:8080/run")
    create.add_argument("--schedule", default="*/30 * * * *", help="Cron schedule expression.")
    create.add_argument("--name", default="curve-gauge-yield-trader", help="Cron job name.")
    create.add_argument("--method", default="POST", help="HTTP method (default: POST).")

    sub.add_parser("list", help="List cron jobs.")

    pause = sub.add_parser("pause", help="Pause a cron job.")
    pause.add_argument("--job-id", required=True, help="Cron job id.")

    resume = sub.add_parser("resume", help="Resume a paused cron job.")
    resume.add_argument("--job-id", required=True, help="Cron job id.")

    delete = sub.add_parser("delete", help="Delete a cron job.")
    delete.add_argument("--job-id", required=True, help="Cron job id.")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        client = _build_client()
        if args.command == "create":
            result = create_job(
                client,
                name=args.name,
                schedule=args.schedule,
                url=args.url,
                method=args.method.upper(),
            )
        elif args.command == "list":
            result = list_jobs(client)
        elif args.command == "pause":
            result = pause_job(client, args.job_id)
        elif args.command == "resume":
            result = resume_job(client, args.job_id)
        elif args.command == "delete":
            result = delete_job(client, args.job_id)
        else:  # pragma: no cover - argparse guards this
            raise RuntimeError(f"Unknown command: {args.command}")
    except (RuntimeError, PublisherError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

