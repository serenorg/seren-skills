#!/usr/bin/env python3
"""
Seren publisher HTTP client.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests


class SerenClient:
    def __init__(self, api_key: Optional[str] = None, gateway_url: Optional[str] = None):
        self.api_key = api_key or os.getenv("SEREN_API_KEY")
        if not self.api_key:
            raise ValueError("SEREN_API_KEY is required")

        self.gateway_url = (gateway_url or os.getenv("SEREN_GATEWAY_URL") or "https://api.serendb.com").rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )

    def call_publisher(
        self,
        publisher: str,
        method: str = "POST",
        path: str = "/",
        body: Optional[Dict[str, Any]] = None,
        query: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        url = f"{self.gateway_url}/publishers/{publisher}{path}"
        req_headers = dict(self.session.headers)
        if headers:
            req_headers.update(headers)

        payload = body
        if query is not None:
            payload = {"query": query}

        kwargs: Dict[str, Any] = {"headers": req_headers, "timeout": timeout}
        if payload is not None:
            kwargs["json"] = payload

        resp = self.session.request(method.upper(), url, **kwargs)
        text = resp.text or ""

        if resp.status_code >= 400:
            raise RuntimeError(f"{publisher} {method} {path} failed: {resp.status_code} {text}")

        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"body": text}

    @staticmethod
    def unwrap_body(resp: Dict[str, Any]) -> Any:
        body = resp.get("body", resp)
        # Some responses double-wrap body as JSON string.
        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body
        return body

    @staticmethod
    def extract_rows(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        body = SerenClient.unwrap_body(resp)
        if isinstance(body, dict):
            if isinstance(body.get("rows"), list):
                return body["rows"]
            if isinstance(body.get("data"), list):
                return body["data"]
            if isinstance(body.get("result"), list):
                return body["result"]
        if isinstance(body, list):
            return body
        return []
