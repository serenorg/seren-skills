#!/usr/bin/env python3
"""Ramp Leveraged Bitcoin Polymarket Deposits (Kraken) — 5x margin via direct Kraken REST API.

Pipeline: Cash → Kraken Ramp (fiat → USDC) → margin buy BTC 5x →
          withdraw USDC → Polygon → Polymarket funded.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# --- Force unbuffered stdout ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

LIVE_SAFETY_VERSION = "2026-03-28.kraken-ramp-leverage-live-safety-v1"
DEFAULT_DRY_RUN = True
DEFAULT_KRAKEN_BASE = "https://api.kraken.com"
DEFAULT_LEVERAGE = 5
MARGIN_PAIR = "XBTUSD"


# ── Logging ──────────────────────────────────────────────────────────

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


# ── Kraken REST API client (direct, no publisher) ───────────────────

class KrakenAPIError(RuntimeError):
    pass


class KrakenClient:
    """Direct Kraken REST client — user supplies own API keys."""

    def __init__(self, api_key: str, api_secret: str,
                 base_url: str = DEFAULT_KRAKEN_BASE,
                 timeout: int = 30) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _sign(self, path: str, data: dict[str, Any]) -> dict[str, str]:
        post_data = urlencode(data)
        nonce = str(data["nonce"])
        encoded = (nonce + post_data).encode("utf-8")
        message = path.encode("utf-8") + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(self.api_secret)
        signature = hmac.new(secret, message, hashlib.sha512)
        return {
            "API-Key": self.api_key,
            "API-Sign": base64.b64encode(signature.digest()).decode("utf-8"),
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }

    def _public(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urlencode(params)
        req = Request(url, method="GET")
        with urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read())
        if payload.get("error"):
            raise KrakenAPIError("; ".join(payload["error"]))
        return payload.get("result", {})

    def _private(self, path: str, data: dict | None = None) -> dict:
        body = dict(data or {})
        body.setdefault("nonce", int(time.time() * 1000))
        headers = self._sign(path, body)
        url = f"{self.base_url}{path}"
        req = Request(url, data=urlencode(body).encode(), headers=headers,
                      method="POST")
        with urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read())
        if payload.get("error"):
            raise KrakenAPIError("; ".join(payload["error"]))
        return payload.get("result", {})

    # ── Public ───────────────────────────────────────────────────────
    def system_status(self) -> dict:
        return self._public("/0/public/SystemStatus")

    def ticker(self, pair: str = MARGIN_PAIR) -> dict:
        return self._public("/0/public/Ticker", {"pair": pair})

    def asset_pairs(self, pair: str = MARGIN_PAIR) -> dict:
        return self._public("/0/public/AssetPairs", {"pair": pair})

    # ── Account ──────────────────────────────────────────────────────
    def balance(self) -> dict:
        return self._private("/0/private/Balance")

    def trade_balance(self, asset: str = "ZUSD") -> dict:
        return self._private("/0/private/TradeBalance", {"asset": asset})

    def open_positions(self) -> dict:
        return self._private("/0/private/OpenPositions")

    # ── Kraken Ramp (fiat onramp) ───────────────────────────────────
    def ramp_payment_methods(self) -> dict:
        return self._public("/b2b/ramp/payment-methods")

    def ramp_quote(self, fiat_amount: str, fiat_currency: str = "USD",
                   crypto_asset: str = "USDC") -> dict:
        return self._public("/b2b/ramp/quotes/prospective", {
            "fiat_amount": fiat_amount,
            "fiat_currency": fiat_currency,
            "crypto_asset": crypto_asset,
        })

    def ramp_checkout(self, quote_id: str) -> dict:
        return self._public("/b2b/ramp/checkout", {"quote_id": quote_id})

    # ── Trading ──────────────────────────────────────────────────────
    def add_order(self, *, pair: str, side: str, ordertype: str,
                  volume: str, leverage: str = "none",
                  price: str | None = None,
                  validate: bool = False) -> dict:
        data: dict[str, Any] = {
            "pair": pair, "type": side, "ordertype": ordertype,
            "volume": volume, "leverage": leverage,
        }
        if price is not None:
            data["price"] = price
        if validate:
            data["validate"] = "true"
        return self._private("/0/private/AddOrder", data)

    # ── Withdrawal ───────────────────────────────────────────────────
    def withdraw_info(self, asset: str, key: str, amount: str) -> dict:
        return self._private("/0/private/WithdrawInfo",
                             {"asset": asset, "key": key, "amount": amount})

    def withdraw(self, asset: str, key: str, amount: str) -> dict:
        return self._private("/0/private/Withdraw",
                             {"asset": asset, "key": key, "amount": amount})


# ── Config bootstrap ─────────────────────────────────────────────────

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

def step_setup(kraken: KrakenClient) -> dict:
    """Validate Kraken connectivity and margin eligibility."""
    _info("Step: setup — validating Kraken environment")

    status = kraken.system_status()
    if status.get("status") != "online":
        _fatal("Kraken system not online", status=status)
    _info("Kraken system online")

    bal = kraken.balance()
    _info("Kraken balance", balance=bal)

    tb = kraken.trade_balance()
    _info("Margin status", equity=tb.get("e"), free_margin=tb.get("mf"))

    pairs = kraken.asset_pairs(MARGIN_PAIR)
    pair_info = next(iter(pairs.values()), {})
    leverage_buy = pair_info.get("leverage_buy", [])
    _info("BTC/USD margin leverage options", leverage_buy=leverage_buy)

    # Verify Kraken Ramp is available
    try:
        ramp_methods = kraken.ramp_payment_methods()
        _info("Kraken Ramp payment methods available", methods=ramp_methods)
    except KrakenAPIError as exc:
        _info(f"Kraken Ramp check failed: {exc} — fiat onramp may not be available")
        ramp_methods = {}

    return {
        "status": "ok", "balance": bal,
        "equity": float(tb.get("e", "0")),
        "free_margin": float(tb.get("mf", "0")),
        "leverage_options": leverage_buy,
        "ramp_available": bool(ramp_methods),
    }


def step_quote_ramp(kraken: KrakenClient, fiat_amount: float,
                    fiat_currency: str = "USD") -> dict:
    """Quote fiat-to-USDC purchase via Kraken Ramp API."""
    _info("Step: quote_ramp — getting fiat-to-USDC quote via Kraken Ramp")
    try:
        quote = kraken.ramp_quote(str(fiat_amount), fiat_currency, "USDC")
        _info("Kraken Ramp quote", quote=quote)
        return {"ramp_quote": quote, "fiat_amount": fiat_amount,
                "fiat_currency": fiat_currency}
    except KrakenAPIError as exc:
        _info(f"Kraken Ramp quote failed: {exc}")
        return {"ramp_quote": None, "error": str(exc),
                "fiat_amount": fiat_amount}


def step_get_btc_price(kraken: KrakenClient) -> float:
    ticker = kraken.ticker(MARGIN_PAIR)
    price = float(next(iter(ticker.values()), {}).get("c", [0])[0])
    _info("BTC/USD price", price=price)
    return price


def step_quote_margin(kraken: KrakenClient, usdc: float,
                      leverage: int, btc_price: float) -> dict:
    """Validate a margin order without executing."""
    _info("Step: quote_margin", usdc=usdc, leverage=leverage)
    position = usdc * leverage
    btc_vol = position / btc_price
    borrowed = position - usdc

    try:
        kraken.add_order(pair=MARGIN_PAIR, side="buy", ordertype="market",
                         volume=f"{btc_vol:.8f}", leverage=str(leverage),
                         validate=True)
        valid = True
    except KrakenAPIError as exc:
        _info(f"Order validation: {exc}")
        valid = False

    return {
        "usdc_collateral": usdc, "leverage": leverage,
        "btc_price": btc_price, "btc_volume": round(btc_vol, 8),
        "position_usd": round(position, 2),
        "borrowed_usd": round(borrowed, 2), "order_valid": valid,
    }


# ── Pipeline orchestrator ────────────────────────────────────────────

def run_pipeline(cfg: dict, dry_run: bool) -> dict:
    api_key = os.environ.get("KRAKEN_API_KEY", "")
    api_secret = os.environ.get("KRAKEN_API_SECRET", "")
    deposit_usd = cfg.get("inputs", {}).get("deposit_amount_usd", 200)
    leverage = cfg.get("inputs", {}).get("leverage", DEFAULT_LEVERAGE)
    withdrawal_key = cfg.get("kraken", {}).get("withdrawal_key_name", "")

    if not api_key or not api_secret:
        _fatal("KRAKEN_API_KEY and KRAKEN_API_SECRET are required")
    if not os.environ.get("POLYMARKET_WALLET_ADDRESS"):
        _fatal("POLYMARKET_WALLET_ADDRESS is required")

    kraken = KrakenClient(api_key, api_secret)
    results: dict[str, Any] = {"dry_run": dry_run, "deposit_usd": deposit_usd,
                                "leverage": leverage}

    setup = step_setup(kraken)
    results["setup"] = setup

    # 1. Quote Kraken Ramp (fiat → USDC)
    ramp = step_quote_ramp(kraken, deposit_usd)
    results["ramp_quote"] = ramp

    btc_price = step_get_btc_price(kraken)
    results["btc_price"] = btc_price

    quote = step_quote_margin(kraken, deposit_usd, leverage, btc_price)
    results["margin_quote"] = quote
    results["estimated_polymarket_deposit"] = quote["borrowed_usd"]

    if dry_run:
        _info("DRY RUN complete — no orders placed, no withdrawals initiated")
        results["status"] = "dry_run_complete"
        print(json.dumps(results, indent=2))
        return results

    # ── LIVE ─────────────────────────────────────────────────────────
    _info("LIVE — placing 5x margin order")
    order = kraken.add_order(
        pair=MARGIN_PAIR, side="buy", ordertype="market",
        volume=f"{quote['btc_volume']:.8f}", leverage=str(leverage),
    )
    results["order"] = order
    _info("Margin order placed", order=order)

    if withdrawal_key:
        w = kraken.withdraw("USDC", withdrawal_key, str(quote["borrowed_usd"]))
        results["withdrawal"] = w
        _info("Withdrawal initiated", withdrawal=w)

    results["status"] = "live_complete"
    print(json.dumps(results, indent=2))
    return results


def run_stop(cfg: dict, dry_run: bool) -> dict:
    api_key = os.environ.get("KRAKEN_API_KEY", "")
    api_secret = os.environ.get("KRAKEN_API_SECRET", "")
    if not api_key or not api_secret:
        _fatal("KRAKEN_API_KEY and KRAKEN_API_SECRET are required")

    kraken = KrakenClient(api_key, api_secret)
    _info("STOP — closing all margin positions")
    positions = kraken.open_positions()

    if dry_run:
        return {"status": "dry_run_stop", "open_positions": len(positions)}

    for txid, pos in positions.items():
        kraken.add_order(
            pair=pos.get("pair", MARGIN_PAIR),
            side="sell" if pos.get("type") == "buy" else "buy",
            ordertype="market", volume=pos.get("vol", "0"),
            leverage=str(pos.get("leverage", "none")),
        )
    result = {"status": "live_stop_complete", "closed": len(positions)}
    print(json.dumps(result, indent=2))
    return result


def run_status(cfg: dict) -> dict:
    api_key = os.environ.get("KRAKEN_API_KEY", "")
    api_secret = os.environ.get("KRAKEN_API_SECRET", "")
    if not api_key or not api_secret:
        _fatal("KRAKEN_API_KEY and KRAKEN_API_SECRET are required")

    kraken = KrakenClient(api_key, api_secret)
    tb = kraken.trade_balance()
    positions = kraken.open_positions()
    result = {
        "status": "ok", "balance": kraken.balance(),
        "equity": float(tb.get("e", "0")),
        "free_margin": float(tb.get("mf", "0")),
        "margin_level": tb.get("ml", "N/A"),
        "open_positions": len(positions), "positions": positions,
    }
    print(json.dumps(result, indent=2))
    return result


# ── CLI ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ramp Leveraged Bitcoin Polymarket Deposits (Kraken)")
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

    {"run": run_pipeline, "status": run_status,
     "stop": lambda c, d: run_stop(c, d)}[args.command](cfg, dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
