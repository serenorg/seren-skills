from __future__ import annotations

import logging

from scripts.secrets import SecretConfig, SecretResolver


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, publisher: str, tool: str, tool_args: dict | None = None):
        self.calls.append((tool, tool_args or {}))
        if tool == "passwords_vaults_list":
            return [
                {"vault_id": "other", "name": "Other"},
                {"vault_id": "vault-1", "name": "Demo Vault"},
            ]
        if tool == "passwords_items_list":
            assert tool_args == {"vault_id": "vault-1"}
            return [
                {"item_id": "item-a", "title": "crm-key"},
                {"item_id": "item-b", "title": "ms-login"},
            ]
        if tool == "passwords_item_get":
            if tool_args["item_id"] == "item-a":
                return {"item": {"primary_value": "  secret-token  "}}
            return {
                "item": {
                    "fields": {
                        "username": "owner@example.com",
                        "password": "password-value",
                        "totp": "totp-value",
                    }
                }
            }
        raise AssertionError(f"unexpected tool {tool}")


def test_secret_resolver_matches_configured_vault_strips_key_and_logs_no_secret(
    caplog,
):
    caplog.set_level(logging.INFO)
    resolver = SecretResolver(
        FakeGateway(),
        SecretConfig(
            vault_name="Demo Vault",
            affinity_item_title="crm-key",
            microsoft_login_item_title="ms-login",
        ),
    )

    assert resolver.get_affinity_key() == "secret-token"
    assert resolver.get_ms_login()["username"] == "owner@example.com"

    logs = caplog.text
    assert "secret-token" not in logs
    assert "password-value" not in logs
    assert "totp-value" not in logs
