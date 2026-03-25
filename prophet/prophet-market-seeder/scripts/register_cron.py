#!/usr/bin/env python3
"""Register the daily seeder as a seren-cron local_pull job."""

from __future__ import annotations

import json
import os
import platform
import sys
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import agent as seeder_agent

SEREN_CRON_PROJECT_ID = "d5a9e489-5e82-4a9b-8e5b-ed45608418e3"
SEREN_CRON_BRANCH_ID = "3df63afa-0924-4163-9327-759fb52da49b"
SEREN_CRON_DB = "serendb"


def get_cron_connection(seren_api_key: str) -> str:
    api = seeder_agent.SerenApi(api_key=seren_api_key)
    conn_str = api.get_connection_string(
        project_id=SEREN_CRON_PROJECT_ID,
        branch_id=SEREN_CRON_BRANCH_ID,
    )
    return seeder_agent._patch_database(conn_str, SEREN_CRON_DB)


def register_runner(conn_str: str, agent_wallet: str) -> str:
    """Register this machine as a seren-cron runner."""
    runner_id = str(uuid.uuid4())
    machine_label = platform.node() or "unknown"
    plat = f"{platform.system().lower()}-{platform.machine()}"

    with seeder_agent.psycopg_connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.runners "
                "(id, agent_wallet, name, machine_label, skill_slug, platform, poll_interval_seconds, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    runner_id,
                    agent_wallet,
                    f"prophet-seeder-{machine_label}",
                    machine_label,
                    "prophet-market-seeder",
                    plat,
                    86400,  # daily
                    "online",
                ),
            )
        conn.commit()
    return runner_id


def register_job(conn_str: str, runner_id: str, agent_wallet: str) -> str:
    """Register the daily seeder job in seren-cron."""
    job_id = str(uuid.uuid4())
    skill_dir = str(SCRIPT_DIR.parent)
    entrypoint = str(SCRIPT_DIR / "daily_seeder.py")

    local_payload = {
        "skill_dir": skill_dir,
        "entrypoint": entrypoint,
        "config_path": str(Path(skill_dir) / "config.json"),
        "command": "daily_seed",
    }

    with seeder_agent.psycopg_connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.jobs "
                "(id, agent_wallet, name, cron_expression, timezone, timeout_seconds, "
                " enabled, execution_mode, runner_id, local_payload, next_run_time) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                " (NOW() AT TIME ZONE 'UTC' + INTERVAL '1 day')::timestamp) RETURNING id",
                (
                    job_id,
                    agent_wallet,
                    "prophet-daily-seeder",
                    "0 14 * * *",  # daily at 2pm UTC
                    "UTC",
                    30,
                    True,
                    "local_pull",
                    runner_id,
                    json.dumps(local_payload),
                ),
            )
        conn.commit()
    return job_id


def main() -> int:
    seren_api_key = os.getenv("SEREN_API_KEY")
    if not seren_api_key:
        print("SEREN_API_KEY required")
        return 1

    agent_wallet = os.getenv("AGENT_WALLET", "0x27e0789225294756b3e3c312dc73d130e95a1665")

    conn_str = get_cron_connection(seren_api_key)
    runner_id = register_runner(conn_str, agent_wallet)
    job_id = register_job(conn_str, runner_id, agent_wallet)

    print(json.dumps({
        "status": "ok",
        "runner_id": runner_id,
        "job_id": job_id,
        "cron": "0 14 * * * (daily at 2pm UTC)",
        "execution_mode": "local_pull",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
