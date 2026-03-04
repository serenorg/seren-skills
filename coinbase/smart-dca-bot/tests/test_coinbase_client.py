from __future__ import annotations

import hashlib
import hmac

from coinbase_client import CoinbaseClient, CoinbaseCredentials


def test_signed_headers_match_ticket_formula() -> None:
    creds = CoinbaseCredentials(api_key="k", api_secret="secret")
    client = CoinbaseClient(credentials=creds)

    method = "POST"
    path = "/api/v3/brokerage/orders"
    timestamp = "1710000000"
    body = '{"a":1}'

    headers = client._signed_headers(  # noqa: SLF001 - intentional unit coverage
        method=method,
        path=path,
        timestamp=timestamp,
        body=body,
    )
    expected = hmac.new(
        b"secret",
        f"{timestamp}{method}{path}{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert headers["CB-ACCESS-SIGN"] == expected


def test_add_order_returns_txid(monkeypatch) -> None:
    creds = CoinbaseCredentials(api_key="k", api_secret="secret")
    client = CoinbaseClient(credentials=creds)

    def _fake_request(*, method, path, params=None, body=None, auth=False):
        assert method == "POST"
        assert path == "/api/v3/brokerage/orders"
        assert auth is True
        assert body is not None
        assert body["product_id"] == "BTC-USD"
        assert body["side"] == "BUY"
        assert "market_market_ioc" in body["order_configuration"]
        return {"success": True, "success_response": {"order_id": "abc123"}}

    monkeypatch.setattr(client, "_request", _fake_request)

    result = client.add_order(
        pair="BTC-USD",
        ordertype="market",
        side="buy",
        volume="0.001",
    )

    assert result == {"txid": ["abc123"]}
