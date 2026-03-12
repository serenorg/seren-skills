#!/usr/bin/env python3
"""Generated SkillForge runtime for peer-to-peer-payments-exchange."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import sys
from urllib.request import Request, urlopen


DEFAULT_DRY_RUN = True
AVAILABLE_CONNECTORS = ['model', 'peer_activity', 'peer_analytics', 'peer_checkout', 'peer_explorer', 'peer_lp', 'peer_market', 'peer_offramp', 'peer_onramp', 'peer_rate_optimizer', 'peer_transfer', 'peer_vault']


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


def run_once(config: dict, dry_run: bool) -> dict:
    return {
        "status": "ok",
        "dry_run": dry_run,
        "connectors": AVAILABLE_CONNECTORS,
        "input_keys": sorted(config.get("inputs", {}).keys()),
    }



# ---------------------------------------------------------------------------
# SerenBucks balance helpers
# ---------------------------------------------------------------------------

from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _runtime_api_key() -> str:
    """Return the Seren API key from environment (Desktop or .env)."""
    for env_name in ("API_KEY", "SEREN_API_KEY"):
        token = (os.getenv(env_name) or "").strip()
        if token:
            return token
    return ""


def _check_serenbucks_balance(api_key: str) -> float:
    """Check SerenBucks balance. Returns balance in USD or 0.0 on error."""
    try:
        request = Request(
            "https://api.serendb.com/wallet/balance",
            headers={
                "User-Agent": "seren-peer-to-peer-payments-exchange/1.0",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            sb = data.get("data") or data.get("serenbucks") or {}
            raw = sb.get("balance_usd") or sb.get("funded_balance_usd") or "0"
            return _safe_float(str(raw).replace("$", "").replace(",", ""), 0.0)
    except Exception as exc:
        print(f"WARNING: could not fetch SerenBucks balance: {exc}", file=sys.stderr)
        return 0.0

def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    result = run_once(config=config, dry_run=dry_run)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
