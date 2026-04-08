#!/usr/bin/env python3
"""Kalshi REST API client with RSA key signing authentication."""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, ec, utils as ec_utils
    from cryptography.hazmat.backends import default_backend

    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False


KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_DEMO_API_BASE = "https://demo-api.kalshi.co/trade-api/v2"

DEFAULT_TIMEOUT = 30


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class KalshiClient:
    """REST client for the Kalshi trading API with RSA key authentication."""

    def __init__(
        self,
        api_key: str | None = None,
        private_key_path: str | None = None,
        private_key_pem: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("KALSHI_API_KEY", "")
        self.base_url = (base_url or os.getenv("KALSHI_API_BASE", KALSHI_API_BASE)).rstrip("/")

        raw_key_path = private_key_path or os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
        raw_key_pem = private_key_pem or os.getenv("KALSHI_PRIVATE_KEY", "")

        self._private_key = None
        if _HAS_CRYPTO:
            if raw_key_pem:
                self._private_key = serialization.load_pem_private_key(
                    raw_key_pem.encode("utf-8"),
                    password=None,
                    backend=default_backend(),
                )
            elif raw_key_path and Path(raw_key_path).exists():
                pem_bytes = Path(raw_key_path).read_bytes()
                self._private_key = serialization.load_pem_private_key(
                    pem_bytes,
                    password=None,
                    backend=default_backend(),
                )

    # ------------------------------------------------------------------
    # Auth / signing
    # ------------------------------------------------------------------

    def _sign_request(self, method: str, path: str, timestamp_ms: int) -> str:
        """Produce the RSA-PSS or ECDSA signature for Kalshi auth headers."""
        if self._private_key is None:
            return ""
        message = f"{timestamp_ms}{method}{path}".encode("utf-8")

        if isinstance(self._private_key, ec.EllipticCurvePrivateKey):
            raw_sig = self._private_key.sign(
                message,
                ec.ECDSA(hashes.SHA256()),
            )
            return base64.b64encode(raw_sig).decode("utf-8")

        # RSA-PSS
        raw_sig = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(raw_sig).decode("utf-8")

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        ts_ms = int(time.time() * 1000)
        sig = self._sign_request(method, path, ts_ms)
        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @property
    def is_authenticated(self) -> bool:
        return bool(self.api_key) and self._private_key is not None

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        auth: bool = True,
    ) -> dict[str, Any] | list[Any]:
        url = f"{self.base_url}{path}"
        headers = self._auth_headers(method, path) if auth else {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(url, data=data, headers=headers, method=method)
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)

    def _get(self, path: str, params: dict[str, Any] | None = None, timeout: int = DEFAULT_TIMEOUT, auth: bool = True) -> Any:
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                path = f"{path}?{urlencode(filtered)}"
        return self._request("GET", path, timeout=timeout, auth=auth)

    def _post(self, path: str, body: dict[str, Any] | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
        return self._request("POST", path, body=body, timeout=timeout)

    def _delete(self, path: str, timeout: int = DEFAULT_TIMEOUT) -> Any:
        return self._request("DELETE", path, timeout=timeout)

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    def get_markets(
        self,
        limit: int = 200,
        cursor: str | None = None,
        event_ticker: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status
        return self._get("/markets", params=params)

    def get_market(self, ticker: str) -> dict[str, Any]:
        return self._get(f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict[str, Any]:
        return self._get(f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_market_history(
        self,
        ticker: str,
        limit: int = 1000,
        min_ts: int | None = None,
        max_ts: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if min_ts is not None:
            params["min_ts"] = min_ts
        if max_ts is not None:
            params["max_ts"] = max_ts
        return self._get(f"/markets/{ticker}/history", params=params)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def get_events(
        self,
        limit: int = 200,
        cursor: str | None = None,
        status: str | None = None,
        with_nested_markets: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": limit,
            "with_nested_markets": str(with_nested_markets).lower(),
        }
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        return self._get("/events", params=params)

    def get_event(self, event_ticker: str, with_nested_markets: bool = True) -> dict[str, Any]:
        params = {"with_nested_markets": str(with_nested_markets).lower()}
        return self._get(f"/events/{event_ticker}", params=params)

    # ------------------------------------------------------------------
    # Portfolio / Orders
    # ------------------------------------------------------------------

    def create_order(
        self,
        ticker: str,
        side: str,
        action: str = "buy",
        count: int = 1,
        type: str = "limit",
        yes_price: int | None = None,
        no_price: int | None = None,
        expiration_ts: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": type,
        }
        if yes_price is not None:
            body["yes_price"] = yes_price
        if no_price is not None:
            body["no_price"] = no_price
        if expiration_ts is not None:
            body["expiration_ts"] = expiration_ts
        return self._post("/portfolio/orders", body=body)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self._delete(f"/portfolio/orders/{order_id}")

    def get_orders(
        self,
        ticker: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        return self._get("/portfolio/orders", params=params)

    def get_fills(
        self,
        ticker: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self._get("/portfolio/fills", params=params)

    # ------------------------------------------------------------------
    # Positions / Balance
    # ------------------------------------------------------------------

    def get_positions(
        self,
        settlement_status: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if settlement_status:
            params["settlement_status"] = settlement_status
        return self._get("/portfolio/positions", params=params)

    def get_balance(self) -> dict[str, Any]:
        return self._get("/portfolio/balance")

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def get_book_metrics(self, ticker: str) -> dict[str, Any]:
        """Return best bid, best ask, spread, and depth from orderbook."""
        book = self.get_orderbook(ticker)
        ob = book.get("orderbook", book)
        yes_bids = ob.get("yes", [])
        no_bids = ob.get("no", [])

        best_yes_bid = 0
        best_yes_bid_size = 0
        total_yes_bid_size = 0
        if yes_bids:
            for level in yes_bids:
                price = _safe_int(level[0] if isinstance(level, list) else level.get("price", 0), 0)
                size = _safe_int(level[1] if isinstance(level, list) else level.get("quantity", 0), 0)
                total_yes_bid_size += size
                if price > best_yes_bid:
                    best_yes_bid = price
                    best_yes_bid_size = size

        best_no_bid = 0
        best_no_bid_size = 0
        total_no_bid_size = 0
        if no_bids:
            for level in no_bids:
                price = _safe_int(level[0] if isinstance(level, list) else level.get("price", 0), 0)
                size = _safe_int(level[1] if isinstance(level, list) else level.get("quantity", 0), 0)
                total_no_bid_size += size
                if price > best_no_bid:
                    best_no_bid = price
                    best_no_bid_size = size

        # On Kalshi: yes_price + no_price = 100 cents
        best_yes_ask = 100 - best_no_bid if best_no_bid > 0 else 0
        spread_cents = best_yes_ask - best_yes_bid if best_yes_bid > 0 and best_yes_ask > 0 else 0

        return {
            "ticker": ticker,
            "best_yes_bid_cents": best_yes_bid,
            "best_yes_bid_size": best_yes_bid_size,
            "best_yes_ask_cents": best_yes_ask,
            "best_no_bid_cents": best_no_bid,
            "best_no_bid_size": best_no_bid_size,
            "spread_cents": max(0, spread_cents),
            "total_yes_bid_depth": total_yes_bid_size,
            "total_no_bid_depth": total_no_bid_size,
        }
