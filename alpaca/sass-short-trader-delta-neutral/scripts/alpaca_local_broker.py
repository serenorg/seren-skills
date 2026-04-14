#!/usr/bin/env python3
"""Local Alpaca REST client for broker operations."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests


DEFAULT_ALPACA_BASE_URL = "https://paper-api.alpaca.markets"


class AlpacaLocalBrokerClient:
    def __init__(
        self,
        api_key_id: str,
        api_secret_key: str,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not api_key_id or not api_secret_key:
            raise ValueError("APCA_API_KEY_ID and APCA_API_SECRET_KEY are required")

        self.base_url = (base_url or os.getenv("APCA_API_BASE_URL") or DEFAULT_ALPACA_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "APCA-API-KEY-ID": api_key_id,
                "APCA-API-SECRET-KEY": api_secret_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    @classmethod
    def from_env(cls) -> Optional["AlpacaLocalBrokerClient"]:
        api_key_id = (
            os.getenv("APCA_API_KEY_ID")
            or os.getenv("ALPACA_API_KEY_ID")
            or os.getenv("APCA_API_KEY")
        )
        api_secret_key = (
            os.getenv("APCA_API_SECRET_KEY")
            or os.getenv("ALPACA_API_SECRET_KEY")
            or os.getenv("APCA_SECRET_KEY")
        )
        if not api_key_id or not api_secret_key:
            return None
        return cls(api_key_id=api_key_id, api_secret_key=api_secret_key)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Any:
        url = f"{self.base_url}{path}"
        kwargs: Dict[str, Any] = {"timeout": timeout}
        if params:
            kwargs["params"] = params
        if body is not None:
            kwargs["json"] = body

        resp = self.session.request(method.upper(), url, **kwargs)
        text = resp.text or ""
        if resp.status_code >= 400:
            raise RuntimeError(f"alpaca local {method.upper()} {path} failed: {resp.status_code} {text}")
        if not text:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"body": text}

    def get_account(self, timeout: int = 30) -> Dict[str, Any]:
        result = self._request("GET", "/v2/account", timeout=timeout)
        if not isinstance(result, dict):
            raise RuntimeError("alpaca local GET /v2/account returned a non-object response")
        return result

    def list_orders(
        self,
        *,
        status: str = "open",
        limit: int = 500,
        nested: bool = False,
        timeout: int = 30,
    ) -> List[Dict[str, Any]]:
        result = self._request(
            "GET",
            "/v2/orders",
            params={"status": status, "limit": limit, "nested": str(nested).lower()},
            timeout=timeout,
        )
        if isinstance(result, list):
            return [row for row in result if isinstance(row, dict)]
        if isinstance(result, dict) and isinstance(result.get("orders"), list):
            return [row for row in result["orders"] if isinstance(row, dict)]
        return []

    def submit_order(self, order: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
        result = self._request("POST", "/v2/orders", body=order, timeout=timeout)
        if not isinstance(result, dict):
            raise RuntimeError("alpaca local POST /v2/orders returned a non-object response")
        return result

    def cancel_order(self, order_id: str, timeout: int = 30) -> Dict[str, Any]:
        result = self._request("DELETE", f"/v2/orders/{order_id}", timeout=timeout)
        if not isinstance(result, dict):
            raise RuntimeError("alpaca local DELETE /v2/orders/{order_id} returned a non-object response")
        return result

    def list_positions(self, timeout: int = 30) -> List[Dict[str, Any]]:
        result = self._request("GET", "/v2/positions", timeout=timeout)
        if isinstance(result, list):
            return [row for row in result if isinstance(row, dict)]
        if isinstance(result, dict) and isinstance(result.get("positions"), list):
            return [row for row in result["positions"] if isinstance(row, dict)]
        return []

    def close_position(self, symbol: str, timeout: int = 30) -> Dict[str, Any]:
        result = self._request("DELETE", f"/v2/positions/{symbol}", timeout=timeout)
        if not isinstance(result, dict):
            raise RuntimeError("alpaca local DELETE /v2/positions/{symbol} returned a non-object response")
        return result
