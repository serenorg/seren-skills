#!/usr/bin/env python3
"""P2P Cash Leveraged Bitcoin Polymarket Deposits — runtime with paper-first live-trading guards.

Pipeline: Cash → ZKP2P → USDC (Base) → cbBTC → Aave V3 supply → borrow USDC →
          Stargate bridge → USDC (Polygon) → Polymarket funded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# --- Force unbuffered stdout so piped/background output is visible immediately ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
# --- End unbuffered stdout fix ---

LIVE_SAFETY_VERSION = "2026-03-28.polymarket-p2p-leverage-live-safety-v1"
DEFAULT_DRY_RUN = True
DEFAULT_API_BASE = "https://api.serendb.com"
DEFAULT_GAS_LIMIT_MULTIPLIER = 1.2
DEFAULT_GAS_PRICE_MULTIPLIER = 1.1

HEX_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
MAX_UINT256 = (1 << 256) - 1

# ── Contract addresses (Base, chain 8453) ────────────────────────────
AAVE_V3_POOL_BASE = "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"
AAVE_V3_DATA_PROVIDER_BASE = "0x2d8A3C5677189723C4cB8873CfC9C8976FDF38Ac"
CBBTC_BASE = "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
AERODROME_ROUTER_BASE = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"

# ── Contract addresses (Polygon, chain 137) ──────────────────────────
USDC_E_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# ── LayerZero / Stargate V2 ──────────────────────────────────────────
LZ_ENDPOINT_ID_BASE = 30184
LZ_ENDPOINT_ID_POLYGON = 30109

# ── ERC-20 / Aave ABI selectors ─────────────────────────────────────
SEL_BALANCE_OF = "0x70a08231"
SEL_APPROVE = "0x095ea7b3"
SEL_ALLOWANCE = "0xdd62ed3e"
SEL_DECIMALS = "0x313ce567"
# Aave V3 Pool
SEL_SUPPLY = "0x617ba037"  # supply(address,uint256,address,uint16)
SEL_BORROW = "0xa415bcad"  # borrow(address,uint256,uint256,uint16,address)
SEL_REPAY = "0x573ade81"   # repay(address,uint256,uint256,address)
SEL_WITHDRAW = "0x69328dec"  # withdraw(address,uint256,address)
SEL_GET_USER_ACCOUNT_DATA = "0xbf92857c"  # getUserAccountData(address)
# Aave V3 PoolDataProvider
SEL_GET_RESERVE_DATA = "0x35ea6a75"
SEL_GET_RESERVE_CONFIG = "0x3e150141"
# Stargate / OFT
SEL_QUOTE_SEND = "0xc7c7f5b3"
SEL_SEND = "0xc7c7f5b3"  # Resolved at setup from Stargate pool


class ConfigError(Exception):
    pass


class PublisherError(Exception):
    pass


# ── Helpers ──────────────────────────────────────────────────────────

def _pad32(hex_str: str) -> str:
    """Left-pad a hex address or value to 32 bytes."""
    clean = hex_str.lower().replace("0x", "")
    return clean.zfill(64)


def _encode_address(addr: str) -> str:
    return _pad32(addr)


def _encode_uint256(val: int) -> str:
    return format(val, "064x")


def _decode_uint256(hex_str: str, offset: int = 0) -> int:
    """Decode a uint256 from a hex string at the given 32-byte slot offset."""
    start = offset * 64
    return int(hex_str[start:start + 64], 16)


def _decode_bool(hex_str: str, offset: int = 0) -> bool:
    return _decode_uint256(hex_str, offset) != 0


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(level: str, msg: str, **kw: Any) -> None:
    entry = {"ts": _ts(), "level": level, "msg": msg, **kw}
    print(json.dumps(entry), file=sys.stderr)


def _info(msg: str, **kw: Any) -> None:
    _log("INFO", msg, **kw)


def _error(msg: str, **kw: Any) -> None:
    _log("ERROR", msg, **kw)


def _fatal(msg: str, **kw: Any) -> None:
    _log("FATAL", msg, **kw)
    sys.exit(1)


# ── Publisher RPC ────────────────────────────────────────────────────

def _rpc_call(api_base: str, api_key: str, publisher: str, method: str,
              params: list, timeout: int = 30) -> Any:
    """Call a Seren publisher with a JSON-RPC payload."""
    url = f"{api_base}/publishers/{publisher}"
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (HTTPError, URLError) as exc:
        raise PublisherError(f"RPC to {publisher} failed: {exc}") from exc

    result_body = data.get("body", data)
    if isinstance(result_body, dict) and "error" in result_body:
        raise PublisherError(
            f"RPC error from {publisher}: {result_body['error']}"
        )
    if isinstance(result_body, dict):
        return result_body.get("result", result_body)
    return result_body


def _eth_call(api_base: str, api_key: str, publisher: str,
              to: str, data: str) -> str:
    """Shorthand for eth_call via publisher."""
    return _rpc_call(api_base, api_key, publisher, "eth_call",
                     [{"to": to, "data": data}, "latest"])


def _eth_chain_id(api_base: str, api_key: str, publisher: str) -> int:
    result = _rpc_call(api_base, api_key, publisher, "eth_chainId", [])
    return int(result, 16)


# ── Config bootstrap ────────────────────────────────────────────────

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


# ── Pipeline steps ───────────────────────────────────────────────────

def step_setup(cfg: dict, api_base: str, api_key: str) -> dict:
    """Validate all dependencies and contracts."""
    _info("Step: setup — validating environment")

    pk = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not pk or len(pk) < 64:
        _fatal("POLYMARKET_PRIVATE_KEY not set or invalid")

    if not api_key:
        _fatal("SEREN_API_KEY not set")

    # Probe Base RPC
    chain_id = _eth_chain_id(api_base, api_key, "seren-base")
    if chain_id != 0x2105:
        _fatal(f"Base chain ID mismatch: got {chain_id:#x}, expected 0x2105")
    _info("Base RPC OK", chain_id=hex(chain_id))

    # Verify cbBTC reserve on Aave V3
    config_data = _eth_call(
        api_base, api_key, "seren-base",
        AAVE_V3_DATA_PROVIDER_BASE,
        SEL_GET_RESERVE_CONFIG + _encode_address(CBBTC_BASE),
    )
    clean = config_data.replace("0x", "")
    ltv = _decode_uint256(clean, 1)
    liq_threshold = _decode_uint256(clean, 2)
    if ltv == 0:
        _fatal("cbBTC has 0% LTV on Aave V3 Base — cannot borrow against it")
    _info("Aave V3 cbBTC reserve OK",
          ltv_bps=ltv, liq_threshold_bps=liq_threshold)

    return {
        "status": "ok",
        "chain_id": hex(chain_id),
        "cbbtc_ltv_bps": ltv,
        "cbbtc_liq_threshold_bps": liq_threshold,
    }


def step_check_balance(cfg: dict, api_base: str, api_key: str,
                       wallet: str) -> dict:
    """Check USDC balance on Base for the Polymarket wallet."""
    _info("Step: check_balance — verifying USDC on Base")

    result = _eth_call(
        api_base, api_key, "seren-base",
        USDC_BASE,
        SEL_BALANCE_OF + _encode_address(wallet),
    )
    balance_raw = int(result.replace("0x", ""), 16)
    balance_usdc = balance_raw / 1e6
    _info("USDC balance on Base", balance=balance_usdc, wallet=wallet)
    return {"usdc_balance_base": balance_usdc, "usdc_balance_raw": balance_raw}


def step_quote_swap(cfg: dict, api_base: str, api_key: str,
                    usdc_amount: float) -> dict:
    """Quote USDC→cbBTC swap on the lowest-spread DEX (Aerodrome or Uniswap V3)."""
    _info("Step: quote_swap — finding best USDC→cbBTC price on Base DEXs")

    usdc_raw = int(usdc_amount * 1e6)
    # Query Aerodrome quoter for USDC→cbBTC
    # getAmountOut(uint256,address,address,bool) = 0xf140a35a
    aerodrome_data = (
        "0xf140a35a"
        + _encode_uint256(usdc_raw)
        + _encode_address(USDC_BASE)
        + _encode_address(CBBTC_BASE)
        + _encode_uint256(0)  # stable=false (volatile pair)
    )
    try:
        result = _eth_call(
            api_base, api_key, "seren-base",
            AERODROME_ROUTER_BASE, aerodrome_data,
        )
        cbbtc_out = int(result.replace("0x", ""), 16)
        cbbtc_amount = cbbtc_out / 1e8
        _info("Aerodrome quote",
              usdc_in=usdc_amount, cbbtc_out=cbbtc_amount)
        return {
            "dex": "aerodrome",
            "usdc_in": usdc_amount,
            "cbbtc_out": cbbtc_amount,
            "cbbtc_out_raw": cbbtc_out,
        }
    except PublisherError as exc:
        _info(f"Aerodrome quote failed ({exc}), would try Uniswap V3 next")
        return {
            "dex": "unknown",
            "usdc_in": usdc_amount,
            "cbbtc_out": 0.0,
            "cbbtc_out_raw": 0,
            "error": str(exc),
        }


def step_quote_aave(cfg: dict, api_base: str, api_key: str,
                    cbbtc_amount: float, ltv_bps: int) -> dict:
    """Estimate USDC borrow amount from Aave V3 given cbBTC collateral."""
    _info("Step: quote_aave — estimating borrow capacity")

    # Get cbBTC price from Aave oracle (via reserve data)
    reserve_data = _eth_call(
        api_base, api_key, "seren-base",
        AAVE_V3_DATA_PROVIDER_BASE,
        SEL_GET_RESERVE_DATA + _encode_address(USDC_BASE),
    )
    clean = reserve_data.replace("0x", "")
    # Available USDC liquidity = totalAToken - totalVariableDebt
    total_atoken = _decode_uint256(clean, 2)
    total_var_debt = _decode_uint256(clean, 4)
    available_usdc = (total_atoken - total_var_debt) / 1e6

    max_borrow_pct = ltv_bps / 10000
    _info("Aave V3 borrow estimate",
          cbbtc_collateral=cbbtc_amount,
          ltv_pct=max_borrow_pct * 100,
          available_usdc=available_usdc)

    return {
        "cbbtc_collateral": cbbtc_amount,
        "ltv_pct": max_borrow_pct * 100,
        "available_usdc_pool": available_usdc,
    }


def step_quote_bridge(cfg: dict, api_base: str, api_key: str,
                      usdc_amount: float) -> dict:
    """Quote Stargate V2 bridge fee for USDC Base→Polygon."""
    _info("Step: quote_bridge — estimating Stargate V2 bridge fee",
          usdc_amount=usdc_amount,
          src="Base (30184)", dst="Polygon (30109)")

    # In dry-run we estimate; live would call quoteSend on the Stargate pool
    estimated_fee_usd = 0.25  # Typical Stargate V2 fee for small amounts
    _info("Bridge fee estimate", fee_usd=estimated_fee_usd)

    return {
        "bridge": "stargate_v2",
        "src_chain": "Base",
        "dst_chain": "Polygon",
        "usdc_amount": usdc_amount,
        "estimated_fee_usd": estimated_fee_usd,
        "lz_dst_eid": LZ_ENDPOINT_ID_POLYGON,
    }


def step_confirm_polygon(cfg: dict, api_base: str, api_key: str,
                         wallet: str) -> dict:
    """Check USDC.e balance on Polygon for the Polymarket wallet."""
    _info("Step: confirm_polygon — checking USDC on Polygon")

    try:
        result = _eth_call(
            api_base, api_key, "seren-polygon",
            USDC_E_POLYGON,
            SEL_BALANCE_OF + _encode_address(wallet),
        )
        balance_raw = int(result.replace("0x", ""), 16)
        balance_usdc = balance_raw / 1e6
        _info("USDC.e balance on Polygon",
              balance=balance_usdc, wallet=wallet)
        return {"usdc_balance_polygon": balance_usdc}
    except PublisherError as exc:
        _info(f"Polygon RPC check failed: {exc}")
        return {"usdc_balance_polygon": None, "error": str(exc)}


# ── Pipeline orchestrator ────────────────────────────────────────────

def run_pipeline(cfg: dict, dry_run: bool) -> dict:
    """Execute the full pipeline: setup → check → quote → (execute if live)."""
    api_base = cfg.get("api", {}).get("base_url", DEFAULT_API_BASE)
    api_key = os.environ.get("SEREN_API_KEY", "")
    deposit_usd = cfg.get("inputs", {}).get("deposit_amount_usd", 200)
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "")

    if not wallet:
        # Derive from private key if not set separately
        pk = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
        if pk:
            wallet = "0x" + "0" * 40  # Placeholder — real derivation needs eth_keys
            _info("Wallet address must be set via POLYMARKET_WALLET_ADDRESS")

    results: dict[str, Any] = {"dry_run": dry_run, "deposit_usd": deposit_usd}

    # 1. Setup
    setup = step_setup(cfg, api_base, api_key)
    results["setup"] = setup

    # 2. Check USDC balance on Base
    if wallet and HEX_ADDRESS_RE.match(wallet):
        balance = step_check_balance(cfg, api_base, api_key, wallet)
        results["balance"] = balance
    else:
        _info("No valid wallet address — skipping balance check")
        results["balance"] = {"usdc_balance_base": None}

    # 3. Quote swap USDC→cbBTC
    swap_quote = step_quote_swap(cfg, api_base, api_key, deposit_usd)
    results["swap_quote"] = swap_quote

    # 4. Quote Aave V3 borrow
    cbbtc_amount = swap_quote.get("cbbtc_out", 0.0)
    ltv_bps = setup.get("cbbtc_ltv_bps", 7300)
    aave_quote = step_quote_aave(cfg, api_base, api_key, cbbtc_amount, ltv_bps)
    results["aave_quote"] = aave_quote

    # 5. Estimate borrowed USDC
    estimated_borrow = cbbtc_amount * 66300 * (ltv_bps / 10000)  # Rough estimate
    results["estimated_borrow_usdc"] = round(estimated_borrow, 2)

    # 6. Quote bridge
    bridge_quote = step_quote_bridge(cfg, api_base, api_key, estimated_borrow)
    results["bridge_quote"] = bridge_quote

    # 7. Final estimate
    net_polymarket = estimated_borrow - bridge_quote.get("estimated_fee_usd", 0)
    results["estimated_polymarket_deposit"] = round(net_polymarket, 2)

    if dry_run:
        _info("DRY RUN complete — no transactions broadcast")
        results["status"] = "dry_run_complete"
        print(json.dumps(results, indent=2))
        return results

    # ── LIVE EXECUTION ───────────────────────────────────────────────
    _info("LIVE mode — transactions will be signed and broadcast")
    _info("Live execution requires wallet signing — not yet implemented in v1")
    _info("Use dry-run output to verify the pipeline, then execute manually")
    results["status"] = "live_not_implemented_v1"
    print(json.dumps(results, indent=2))
    return results


def run_stop(cfg: dict, dry_run: bool) -> dict:
    """Emergency exit: repay Aave debt and withdraw collateral."""
    api_base = cfg.get("api", {}).get("base_url", DEFAULT_API_BASE)
    api_key = os.environ.get("SEREN_API_KEY", "")
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "")

    _info("STOP — emergency exit initiated")

    if dry_run:
        _info("DRY RUN stop — would repay all USDC debt and withdraw cbBTC")
        return {"status": "dry_run_stop", "action": "would_repay_and_withdraw"}

    _info("Live stop requires wallet signing — not yet implemented in v1")
    return {"status": "live_stop_not_implemented_v1"}


def run_status(cfg: dict) -> dict:
    """Check current Aave position and Polygon balance."""
    api_base = cfg.get("api", {}).get("base_url", DEFAULT_API_BASE)
    api_key = os.environ.get("SEREN_API_KEY", "")
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "")

    _info("STATUS — checking current positions")

    results: dict[str, Any] = {}

    if wallet and HEX_ADDRESS_RE.match(wallet):
        # Check Aave V3 position
        try:
            account_data = _eth_call(
                api_base, api_key, "seren-base",
                AAVE_V3_POOL_BASE,
                SEL_GET_USER_ACCOUNT_DATA + _encode_address(wallet),
            )
            clean = account_data.replace("0x", "")
            total_collateral = _decode_uint256(clean, 0) / 1e8
            total_debt = _decode_uint256(clean, 1) / 1e8
            available_borrow = _decode_uint256(clean, 2) / 1e8
            health_factor = _decode_uint256(clean, 5) / 1e18

            results["aave_position"] = {
                "total_collateral_usd": total_collateral,
                "total_debt_usd": total_debt,
                "available_borrow_usd": available_borrow,
                "health_factor": health_factor,
            }
        except PublisherError as exc:
            results["aave_position"] = {"error": str(exc)}

        # Check Polygon balance
        polygon = step_confirm_polygon(cfg, api_base, api_key, wallet)
        results["polygon_balance"] = polygon
    else:
        results["error"] = "No valid POLYMARKET_WALLET_ADDRESS set"

    results["status"] = "ok"
    print(json.dumps(results, indent=2))
    return results


# ── CLI ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P2P Cash Leveraged Bitcoin Polymarket Deposits",
    )
    parser.add_argument(
        "command", nargs="?", default="run",
        choices=["run", "status", "stop"],
        help="Command to execute (default: run)",
    )
    parser.add_argument(
        "--config", default="config.json",
        help="Path to runtime config file (default: config.json)",
    )
    parser.add_argument(
        "--yes-live", action="store_true", default=False,
        help="Enable live execution (without this flag, runs in dry-run mode)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = _bootstrap_config_path(args.config)

    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        cfg = {}

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
