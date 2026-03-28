#!/usr/bin/env python3
"""P2P Hyperliquid Bitcoin USDC Polymarket Deposit — 5x BTC-PERP, no debt, self-custody.

Pipeline: Cash → ZKP2P → USDC → Hyperliquid → 5x BTC-PERP → withdraw free USDC →
          CCTP → Polygon → Polymarket funded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- Force unbuffered stdout ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

LIVE_SAFETY_VERSION = "2026-03-28.hyperliquid-p2p-polymarket-v1"
DEFAULT_DRY_RUN = True
DEFAULT_LEVERAGE = 5
DEFAULT_API_BASE = "https://api.serendb.com"
HYPERLIQUID_API = "https://api.hyperliquid.xyz"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(level: str, msg: str, **kw: Any) -> None:
    print(json.dumps({"ts": _ts(), "level": level, "msg": msg, **kw}),
          file=sys.stderr)


def _info(msg: str, **kw: Any) -> None:
    _log("INFO", msg, **kw)


def _fatal(msg: str, **kw: Any) -> None:
    _log("FATAL", msg, **kw)
    sys.exit(1)


def _bootstrap_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists():
        return path
    example = path.parent / "config.example.json"
    if example.exists():
        import shutil
        shutil.copy2(example, path)
        _info("Copied config.example.json → config.json")
    return path


def run_pipeline(cfg: dict, dry_run: bool) -> dict:
    api_base = cfg.get("api", {}).get("base_url", DEFAULT_API_BASE)
    api_key = os.environ.get("SEREN_API_KEY", "")
    pk = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    deposit_usd = cfg.get("inputs", {}).get("deposit_amount_usd", 200)
    leverage = cfg.get("inputs", {}).get("leverage", DEFAULT_LEVERAGE)

    if not pk:
        _fatal("POLYMARKET_PRIVATE_KEY is required")
    if not api_key:
        _fatal("SEREN_API_KEY is required (for CoinGecko backtest)")

    margin = deposit_usd / leverage
    btc_notional = deposit_usd
    free_usdc = deposit_usd - margin - 1  # 1 USDC withdrawal fee
    liq_drop_pct = ((1 / leverage) - 0.03) * 100

    results: dict[str, Any] = {
        "dry_run": dry_run,
        "deposit_usd": deposit_usd,
        "leverage": leverage,
        "margin_locked": round(margin, 2),
        "btc_notional": round(btc_notional, 2),
        "free_usdc": round(free_usdc, 2),
        "to_polymarket": round(free_usdc, 2),
        "debt": 0,
        "liq_threshold_pct": round(liq_drop_pct, 1),
    }

    # Run 270-day liquidation backtest
    _info("Running 270-day liquidation risk backtest via CoinGecko...")
    try:
        from backtest import _fetch_btc_prices, run_backtest, format_backtest_report
        prices = _fetch_btc_prices(api_base, api_key)
        bt = run_backtest(prices, leverage)
        results["backtest"] = bt
        _info("\n" + format_backtest_report(bt))
    except Exception as exc:
        _info(f"Backtest failed: {exc}")
        results["backtest"] = {"error": str(exc)}

    if dry_run:
        _info("DRY RUN complete — no positions opened")
        results["status"] = "dry_run_complete"
        print(json.dumps(results, indent=2))
        return results

    _info("LIVE mode — Hyperliquid perp execution not yet implemented in v1")
    results["status"] = "live_not_implemented_v1"
    print(json.dumps(results, indent=2))
    return results


def run_stop(cfg: dict, dry_run: bool) -> dict:
    _info("STOP — would close BTC-PERP and withdraw remaining margin")
    return {"status": "dry_run_stop" if dry_run else "stop_not_implemented_v1"}


def run_status(cfg: dict) -> dict:
    _info("STATUS — would query Hyperliquid account positions")
    return {"status": "not_implemented_v1"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="P2P Hyperliquid Bitcoin USDC Polymarket Deposit")
    p.add_argument("command", nargs="?", default="run",
                   choices=["run", "status", "stop"])
    p.add_argument("--config", default="config.json")
    p.add_argument("--yes-live", action="store_true", default=False)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    path = _bootstrap_config_path(args.config)
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    dry_run = not args.yes_live and bool(cfg.get("dry_run", DEFAULT_DRY_RUN))

    if args.command == "status":
        run_status(cfg)
    elif args.command == "stop":
        run_stop(cfg, dry_run)
    else:
        run_pipeline(cfg, dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
