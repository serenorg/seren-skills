#!/usr/bin/env python3
"""
Setup seren-cron job for autonomous Polymarket trading.

This script registers a cron job with seren-cron to trigger the trading agent
on a regular schedule (default: every 10 minutes).

Prerequisites:
    1. Agent server must be running: python scripts/run_agent_server.py --config config.json
    2. Server must be accessible at the URL provided

Usage:
    python scripts/setup_cron.py --url http://localhost:8080/run [--schedule "*/10 * * * *"]
"""

import argparse
import sys
import os
from seren_client import SerenClient


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Setup seren-cron for Polymarket bot')
    parser.add_argument(
        '--url',
        required=True,
        help='URL of the running agent server (e.g., http://localhost:8080/run)'
    )
    parser.add_argument(
        '--schedule',
        default='*/10 * * * *',
        help='Cron schedule expression (default: */10 * * * * = every 10 min)'
    )
    parser.add_argument(
        '--name',
        default='polymarket-trading-bot',
        help='Job name (default: polymarket-trading-bot)'
    )

    args = parser.parse_args()

    # Initialize Seren client
    try:
        print("Initializing Seren client...")
        seren = SerenClient()
        print("✓ Seren client initialized")
        print()
    except Exception as e:
        print(f"Error initializing Seren client: {e}")
        print("\nMake sure SEREN_API_KEY is set in your .env file or environment.")
        sys.exit(1)

    # Create cron job
    try:
        print(f"Creating cron job...")
        print(f"  Name: {args.name}")
        print(f"  Schedule: {args.schedule}")
        print(f"  URL: {args.url}")
        print()

        result = seren.create_cron_job(
            name=args.name,
            schedule=args.schedule,
            url=args.url,
            method='POST'
        )

        job_id = result.get('job_id') or result.get('id')

        print("✅ Cron job created successfully!")
        print(f"   Job ID: {job_id}")
        print()
        print("Your Polymarket trading bot is now running autonomously!")
        print()
        print("Management commands:")
        print(f"  - Pause job:  python -c 'from seren_client import SerenClient; SerenClient().pause_cron_job(\"{job_id}\")'")
        print(f"  - Resume job: python -c 'from seren_client import SerenClient; SerenClient().resume_cron_job(\"{job_id}\")'")
        print(f"  - Delete job: python -c 'from seren_client import SerenClient; SerenClient().delete_cron_job(\"{job_id}\")'")
        print()

    except Exception as e:
        print(f"❌ Error creating cron job: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
