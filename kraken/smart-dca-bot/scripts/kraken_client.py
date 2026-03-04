#!/usr/bin/env python3
"""Direct Kraken REST API client (no Seren trading proxy)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class KrakenAPIError(RuntimeError):
    """Raised when Kraken API returns an error."""


@dataclass
class KrakenCredentials:
    api_key: str
    api_secret: str


class KrakenClient:
    """Minimal Kraken REST client with public and private endpoints."""

    def __init__(
        self,
        credentials: KrakenCredentials,
        base_url: str = "https://api.kraken.com",
        timeout_seconds: int = 30,
    ) -> None:
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _signed_headers(self, path: str, data: dict[str, Any]) -> dict[str, str]:
        post_data = urlencode(data)
        nonce = str(data["nonce"])
        encoded = (nonce + post_data).encode("utf-8")
        message = path.encode("utf-8") + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(self.credentials.api_secret)
        signature = hmac.new(secret, message, hashlib.sha512)
        signature_b64 = base64.b64encode(signature.digest()).decode("utf-8")
        return {
            "API-Key": self.credentials.api_key,
            "API-Sign": signature_b64,
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }

    def _call_public(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if requests is None:
            raise KrakenAPIError("requests dependency is required for non-mock Kraken calls")
        url = f"{self.base_url}{path}"
        response = requests.get(url, params=params or {}, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("error") or []
        if errors:
            raise KrakenAPIError("; ".join(errors))
        return payload.get("result", {})

    def _call_private(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        if requests is None:
            raise KrakenAPIError("requests dependency is required for non-mock Kraken calls")
        body = dict(data or {})
        body.setdefault("nonce", int(time.time() * 1000))
        headers = self._signed_headers(path, body)
        url = f"{self.base_url}{path}"
        response = requests.post(url, data=body, headers=headers, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("error") or []
        if errors:
            raise KrakenAPIError("; ".join(errors))
        return payload.get("result", {})

    def get_ticker(self, pair: str) -> dict[str, Any]:
        return self._call_public("/0/public/Ticker", {"pair": pair})

    def get_ohlc(self, pair: str, interval: int = 15, since: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"pair": pair, "interval": interval}
        if since is not None:
            params["since"] = since
        return self._call_public("/0/public/OHLC", params)

    def get_depth(self, pair: str, count: int = 50) -> dict[str, Any]:
        return self._call_public("/0/public/Depth", {"pair": pair, "count": count})

    def get_asset_pairs(self) -> dict[str, Any]:
        return self._call_public("/0/public/AssetPairs")

    def get_assets(self) -> dict[str, Any]:
        return self._call_public("/0/public/Assets")

    def get_balance(self) -> dict[str, Any]:
        return self._call_private("/0/private/Balance")

    def add_order(
        self,
        *,
        pair: str,
        ordertype: str,
        side: str,
        volume: str,
        price: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pair": pair,
            "type": side,
            "ordertype": ordertype,
            "volume": volume,
        }
        if price is not None:
            payload["price"] = price
        return self._call_private("/0/private/AddOrder", payload)

    def cancel_order(self, txid: str) -> dict[str, Any]:
        return self._call_private("/0/private/CancelOrder", {"txid": txid})

    def open_orders(self) -> dict[str, Any]:
        return self._call_private("/0/private/OpenOrders")

    def trades_history(self) -> dict[str, Any]:
        return self._call_private("/0/private/TradesHistory")
