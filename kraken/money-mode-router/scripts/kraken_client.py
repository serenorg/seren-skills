"""Kraken publisher wrapper for Money Mode Router context collection."""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests


class KrakenClient:
    """Reads account and market context from Kraken via Seren Gateway."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.serendb.com",
        publisher: str = "kraken-trading",
        fallback_publisher: str = "kraken-spot-trading",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.publishers = [publisher, fallback_publisher]

    def _call(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        last_error: Optional[Exception] = None
        for idx, publisher in enumerate(self.publishers):
            url = f"{self.base_url}/publishers/{publisher}{path}"
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=body,
                    params=params,
                    timeout=30,
                )
                if response.status_code >= 400:
                    if response.status_code in (401, 403):
                        if idx < len(self.publishers) - 1:
                            raise requests.HTTPError("unauthorized", response=response)
                        raise requests.HTTPError(
                            "Kraken publisher authentication failed. Configure the Kraken "
                            "publisher key in Seren Desktop Settings and ensure it is enabled.",
                            response=response,
                        )
                    response.raise_for_status()

                payload = response.json()
                if isinstance(payload, dict) and "body" in payload:
                    return payload["body"]
                return payload
            except requests.HTTPError as exc:
                if getattr(exc.response, "status_code", None) in (401, 403, 404):
                    last_error = exc
                    continue
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise RuntimeError("No Kraken publisher configured")

    def get_balance(self) -> Dict[str, Any]:
        return self._call(method="POST", path="/private/Balance", body={})

    def get_open_orders(self) -> Dict[str, Any]:
        return self._call(method="POST", path="/private/OpenOrders", body={})

    def get_ticker(self, pair: str = "XBTUSD") -> Dict[str, Any]:
        return self._call(method="GET", path="/public/Ticker", params={"pair": pair})

    def get_account_snapshot(self) -> Dict[str, Any]:
        """Best-effort account snapshot used as recommendation context."""
        snapshot: Dict[str, Any] = {
            "balances": {},
            "open_order_count": 0,
            "market_hint": {},
            "errors": [],
        }

        try:
            balances = self.get_balance()
            snapshot["balances"] = balances.get("result", balances)
        except Exception as exc:  # noqa: BLE001
            snapshot["errors"].append(f"balance_error: {exc}")

        try:
            open_orders = self.get_open_orders()
            open_orders_result = open_orders.get("result", {}).get("open", {})
            snapshot["open_order_count"] = len(open_orders_result)
        except Exception as exc:  # noqa: BLE001
            snapshot["errors"].append(f"open_orders_error: {exc}")

        try:
            ticker = self.get_ticker("XBTUSD")
            ticker_result = ticker.get("result", {})
            if ticker_result:
                first_key = list(ticker_result.keys())[0]
                snapshot["market_hint"] = {
                    "pair": first_key,
                    "last_trade": ticker_result[first_key].get("c", [None])[0],
                }
        except Exception as exc:  # noqa: BLE001
            snapshot["errors"].append(f"ticker_error: {exc}")

        return snapshot
