#!/usr/bin/env python3
"""Direct Coinbase Advanced Trade API client (no Seren trading proxy)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class CoinbaseAPIError(RuntimeError):
    """Raised when Coinbase API returns an error."""


@dataclass
class CoinbaseCredentials:
    api_key: str
    api_secret: str


_GRANULARITY_MAP = {
    1: "ONE_MINUTE",
    5: "FIVE_MINUTE",
    15: "FIFTEEN_MINUTE",
    30: "THIRTY_MINUTE",
    60: "ONE_HOUR",
    120: "TWO_HOUR",
    360: "SIX_HOUR",
    1440: "ONE_DAY",
}


class CoinbaseClient:
    """Minimal Coinbase Advanced Trade client with public and private endpoints."""

    def __init__(
        self,
        credentials: CoinbaseCredentials,
        base_url: str = "https://api.coinbase.com",
        timeout_seconds: int = 30,
    ) -> None:
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _json_dumps(body: dict[str, Any] | None) -> str:
        if not body:
            return ""
        return json.dumps(body, separators=(",", ":"), sort_keys=True)

    def _signed_headers(
        self,
        *,
        method: str,
        path: str,
        timestamp: str,
        body: str,
    ) -> dict[str, str]:
        # Ticket contract: timestamp + method + path + body, HMAC-SHA256 hex.
        payload = f"{timestamp}{method.upper()}{path}{body}"
        signature = hmac.new(
            self.credentials.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "CB-ACCESS-KEY": self.credentials.api_key,
            "CB-ACCESS-SIGN": signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = False,
    ) -> dict[str, Any]:
        if requests is None:
            raise CoinbaseAPIError("requests dependency is required for non-mock Coinbase calls")

        url = f"{self.base_url}{path}"
        body_str = self._json_dumps(body)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth:
            ts = str(int(time.time()))
            headers.update(
                self._signed_headers(
                    method=method,
                    path=path,
                    timestamp=ts,
                    body=body_str,
                )
            )

        response = requests.request(
            method=method,
            url=url,
            params=params or None,
            data=body_str if body_str else None,
            headers=headers,
            timeout=self.timeout_seconds,
        )

        content_type = response.headers.get("Content-Type", "")
        payload: dict[str, Any] = {}
        if "application/json" in content_type:
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    payload = {"data": parsed}
            except ValueError:
                payload = {}

        if response.status_code >= 400:
            detail = payload.get("message") if isinstance(payload, dict) else ""
            if not detail:
                detail = response.text[:200]
            raise CoinbaseAPIError(
                f"Coinbase API error {response.status_code}: {detail}"
            )

        return payload

    @staticmethod
    def _float(value: Any, fallback: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def get_ticker(self, pair: str) -> dict[str, Any]:
        payload = self._request(
            method="GET",
            path=f"/api/v3/brokerage/products/{pair}",
            auth=False,
        )
        product = payload.get("product") if isinstance(payload.get("product"), dict) else payload

        price = self._float(product.get("price"), 0.0)
        bid = self._float(product.get("best_bid"), price)
        ask = self._float(product.get("best_ask"), price)
        change_pct = self._float(product.get("price_percentage_change_24h"), 0.0)
        if abs(change_pct) > 1.0:
            change_pct /= 100.0
        high_24h = max(price * (1.0 + max(change_pct, 0.0)), price)
        low_24h = min(price * (1.0 + min(change_pct, 0.0)), price)
        volume_24h = self._float(product.get("approximate_quote_24h_volume"), 0.0)
        if volume_24h <= 0:
            volume_24h = self._float(product.get("volume_24h"), 0.0) * max(price, 1e-9)

        row = {
            "c": [f"{price:.12f}"],
            "p": [f"{price:.12f}", f"{price:.12f}"],
            "b": [f"{bid:.12f}"],
            "a": [f"{ask:.12f}"],
            "l": [f"{price:.12f}", f"{low_24h:.12f}"],
            "h": [f"{price:.12f}", f"{high_24h:.12f}"],
            "v": ["0", f"{volume_24h:.12f}"],
        }
        return {pair: row}

    def get_ohlc(self, pair: str, interval: int = 15, since: int | None = None) -> dict[str, Any]:
        granularity = _GRANULARITY_MAP.get(interval, "FIFTEEN_MINUTE")
        params: dict[str, Any] = {"granularity": granularity, "limit": 350}
        if since is not None:
            params["start"] = int(since)

        payload = self._request(
            method="GET",
            path=f"/api/v3/brokerage/products/{pair}/candles",
            params=params,
            auth=False,
        )
        rows = payload.get("candles", []) if isinstance(payload.get("candles"), list) else []

        candles: list[list[str]] = []
        for row in rows:
            start = int(self._float(row.get("start"), 0.0))
            low = self._float(row.get("low"), 0.0)
            high = self._float(row.get("high"), 0.0)
            open_px = self._float(row.get("open"), 0.0)
            close = self._float(row.get("close"), 0.0)
            volume = self._float(row.get("volume"), 0.0)
            candles.append(
                [
                    str(start),
                    f"{open_px:.12f}",
                    f"{high:.12f}",
                    f"{low:.12f}",
                    f"{close:.12f}",
                    "0",
                    f"{volume:.12f}",
                ]
            )

        candles.sort(key=lambda item: int(float(item[0])))
        last = candles[-1][0] if candles else "0"
        return {pair: candles, "last": last}

    def get_depth(self, pair: str, count: int = 50) -> dict[str, Any]:
        payload = self._request(
            method="GET",
            path="/api/v3/brokerage/product_book",
            params={"product_id": pair, "limit": int(max(count, 1))},
            auth=False,
        )

        def _levels(raw: Any) -> list[list[str]]:
            levels: list[list[str]] = []
            if not isinstance(raw, list):
                return levels
            for item in raw:
                if not isinstance(item, dict):
                    continue
                price = self._float(item.get("price"), 0.0)
                size = self._float(item.get("size"), 0.0)
                if price <= 0 or size <= 0:
                    continue
                levels.append([f"{price:.12f}", f"{size:.12f}", "0"])
            return levels

        return {
            pair: {
                "bids": _levels(payload.get("bids")),
                "asks": _levels(payload.get("asks")),
            }
        }

    def get_asset_pairs(self) -> dict[str, Any]:
        payload = self._request(
            method="GET",
            path="/api/v3/brokerage/products",
            params={"limit": 500},
            auth=False,
        )
        products = payload.get("products", []) if isinstance(payload.get("products"), list) else []
        out: dict[str, Any] = {}
        for product in products:
            if not isinstance(product, dict):
                continue
            product_id = str(product.get("product_id", "")).upper().strip()
            if product_id:
                out[product_id] = product
        return out

    def get_assets(self) -> dict[str, Any]:
        payload = self._request(
            method="GET",
            path="/api/v3/brokerage/currencies",
            auth=False,
        )
        currencies = payload.get("currencies", []) if isinstance(payload.get("currencies"), list) else []
        out: dict[str, Any] = {}
        for currency in currencies:
            if not isinstance(currency, dict):
                continue
            code = str(currency.get("id") or currency.get("symbol") or "").upper().strip()
            if code:
                out[code] = currency
        return out

    def get_balance(self) -> dict[str, Any]:
        payload = self._request(
            method="GET",
            path="/api/v3/brokerage/accounts",
            params={"limit": 250},
            auth=True,
        )
        accounts = payload.get("accounts", []) if isinstance(payload.get("accounts"), list) else []
        balances: dict[str, float] = {}
        for account in accounts:
            if not isinstance(account, dict):
                continue
            currency = str(account.get("currency") or "").upper().strip()
            available = account.get("available_balance", {}) if isinstance(account.get("available_balance"), dict) else {}
            value = self._float(available.get("value"), 0.0)
            if currency:
                balances[currency] = balances.get(currency, 0.0) + value
        return balances

    def add_order(
        self,
        *,
        pair: str,
        ordertype: str,
        side: str,
        volume: str,
        price: str | None = None,
    ) -> dict[str, Any]:
        ordertype = ordertype.lower().strip()
        side = side.upper().strip()
        base_size = self._float(volume, 0.0)
        if base_size <= 0:
            raise CoinbaseAPIError("order volume must be > 0")

        order_configuration: dict[str, Any]
        if ordertype == "limit":
            limit_price = self._float(price, 0.0)
            if limit_price <= 0:
                raise CoinbaseAPIError("limit price must be > 0")
            order_configuration = {
                "limit_limit_gtc": {
                    "base_size": f"{base_size:.8f}",
                    "limit_price": f"{limit_price:.8f}",
                    "post_only": True,
                }
            }
        else:
            order_configuration = {
                "market_market_ioc": {
                    "base_size": f"{base_size:.8f}",
                }
            }

        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": pair,
            "side": side,
            "order_configuration": order_configuration,
        }
        payload = self._request(
            method="POST",
            path="/api/v3/brokerage/orders",
            body=body,
            auth=True,
        )
        success = payload.get("success")
        if success is False:
            raise CoinbaseAPIError(
                f"Coinbase order rejected: {payload.get('error_response') or payload}"
            )

        order_id = ""
        if isinstance(payload.get("success_response"), dict):
            order_id = str(payload["success_response"].get("order_id", "")).strip()
        order_id = order_id or str(payload.get("order_id", "")).strip()
        return {"txid": [order_id] if order_id else []}

    def cancel_order(self, txid: str) -> dict[str, Any]:
        return self._request(
            method="POST",
            path="/api/v3/brokerage/orders/batch_cancel",
            body={"order_ids": [txid]},
            auth=True,
        )

    def open_orders(self) -> dict[str, Any]:
        return self._request(
            method="GET",
            path="/api/v3/brokerage/orders/historical/batch",
            params={"order_status": "OPEN", "limit": 250},
            auth=True,
        )

    def trades_history(self) -> dict[str, Any]:
        return self._request(
            method="GET",
            path="/api/v3/brokerage/orders/historical/fills",
            params={"limit": 250},
            auth=True,
        )

    def get_learn_rewards(self) -> dict[str, float]:
        # Optional local override for deterministic operation without OAuth.
        raw = os.getenv("COINBASE_LEARN_REWARDS_JSON", "").strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(payload, dict):
            return {}
        rewards: dict[str, float] = {}
        for key, value in payload.items():
            asset = str(key).upper().strip()
            if not asset:
                continue
            rewards[asset] = max(self._float(value, 0.0), 0.0)
        return rewards

    def get_stakeable_assets(self) -> dict[str, float]:
        # Optional local override for deterministic operation.
        raw = os.getenv("COINBASE_STAKING_APY_JSON", "").strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(payload, dict):
            return {}
        apys: dict[str, float] = {}
        for key, value in payload.items():
            asset = str(key).upper().strip()
            if not asset:
                continue
            apys[asset] = max(self._float(value, 0.0), 0.0)
        return apys
