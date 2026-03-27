#!/usr/bin/env python3
"""Family-office knowledge skill runtime with affiliate reward webhook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from affiliates_webhook import fire_reward_webhook

DEFAULT_DRY_RUN = True
AVAILABLE_CONNECTORS = ['asana', 'docreader', 'sharepoint', 'storage']


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run generated SkillForge agent runtime.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    return parser.parse_args()


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def calculate_rewards(config: dict, event_type: str = "knowledge_capture") -> dict:
    """Step 14: Calculate and fire affiliate reward webhook.

    Fires an HMAC-signed webhook to seren-affiliates so the employee
    earns SerenBucks for the knowledge event. Non-blocking — failures
    are logged but do not crash the session.

    Args:
        config: Skill config dict
        event_type: "knowledge_capture" or "knowledge_retrieval"

    Returns:
        Reward result dict with status, transaction_id, and any errors
    """
    inputs = config.get("inputs", {})
    agent_id = config.get("agent_id", inputs.get("agent_id", ""))
    referral_code = config.get("referral_code", inputs.get("referral_code", ""))

    if event_type == "knowledge_retrieval":
        amount_cents = int(inputs.get("reward_per_retrieval_usd", 1) * 100)
    else:
        amount_cents = int(inputs.get("reward_base_usd", 100) * 100)

    test_mode = bool(config.get("dry_run", DEFAULT_DRY_RUN))

    try:
        result = fire_reward_webhook(
            config=config,
            agent_id=agent_id,
            referral_code=referral_code,
            event_type=event_type,
            amount_cents=amount_cents,
            test_mode=test_mode,
        )
    except Exception as e:
        result = {"status": "error", "error": str(e)}

    return result


def persist_rewards(reward_result: dict) -> dict:
    """Step 15: Log the reward webhook response for audit.

    Returns the reward result unchanged for downstream consumption.
    """
    status = reward_result.get("status", "unknown")
    tx_id = reward_result.get("transaction_id", "")

    if status == "sent":
        print(f"  Reward webhook sent: transaction_id={tx_id}")
    elif status == "skipped":
        print(f"  Reward webhook skipped: {reward_result.get('reason', '')}")
    elif status == "failed":
        print(f"  Reward webhook failed: {reward_result.get('error', '')} (transaction_id={tx_id})")
    elif status == "error":
        print(f"  Reward webhook error: {reward_result.get('error', '')}")

    return reward_result


def run_once(config: dict, dry_run: bool) -> dict:
    result = {
        "status": "ok",
        "dry_run": dry_run,
        "connectors": AVAILABLE_CONNECTORS,
        "input_keys": sorted(config.get("inputs", {}).keys()),
    }

    # Steps 14-15: Calculate and persist rewards (non-blocking)
    reward = calculate_rewards(config, event_type="knowledge_capture")
    audit = persist_rewards(reward)
    result["reward"] = audit

    return result


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    result = run_once(config=config, dry_run=dry_run)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
