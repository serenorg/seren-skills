from __future__ import annotations

import logging

import pytest

from scripts.secrets import SecretConfig, SecretResolver, SetupBlocked
from scripts.seren_client import PublisherError


class ExplodingGateway:
    """Any tool call is a failure — proves env-first needs no gateway."""

    def call_tool(self, publisher, tool, tool_args=None):
        raise AssertionError(f"gateway must not be called; got {tool!r}")


class HostedPasswordsGateway:
    """Hosted Seren Passwords MCP tools returning plaintext (desktop)."""

    def call_tool(self, publisher, tool, tool_args=None):
        if tool == "passwords_vaults_list":
            return [{"vault_id": "vault-1", "name": "Demo Vault"}]
        if tool == "passwords_items_list":
            return [{"item_id": "item-a", "title": "crm-key"}]
        if tool == "passwords_item_get":
            return {"item": {"primary_value": "  secret-token  "}}
        raise AssertionError(f"unexpected tool {tool}")


class EncryptedOnlyGateway:
    """REST surface returns only E2E-encrypted records (the #861 path)."""

    def call_tool(self, publisher, tool, tool_args=None):
        if tool in {"passwords_vaults_list", "passwords_items_list", "passwords_item_get"}:
            raise PublisherError(403, "Endpoint is not in the allowed endpoints list")
        if tool == "get_vaults":
            return {"data": [{"vault_id": "vault-1", "name_ciphertext": "encrypted"}]}
        raise AssertionError(tool)


def _config() -> SecretConfig:
    return SecretConfig(vault_name="Demo Vault", affinity_item_title="crm-key")


def test_affinity_key_resolves_from_env_first_without_gateway(caplog):
    caplog.set_level(logging.INFO)
    resolver = SecretResolver(
        ExplodingGateway(),
        _config(),
        env={"AFFINITY_API_KEY": "  env-token  "},
    )
    assert resolver.get_affinity_key() == "env-token"
    assert "env-token" not in caplog.text  # never log the secret


def test_affinity_key_falls_back_to_hosted_passwords_tools():
    resolver = SecretResolver(HostedPasswordsGateway(), _config(), env={})
    assert resolver.get_affinity_key() == "secret-token"


def test_setup_blocked_names_env_var_when_no_env_and_no_gateway():
    resolver = SecretResolver(None, _config(), env={})
    with pytest.raises(SetupBlocked) as exc:
        resolver.get_affinity_key()
    assert "AFFINITY_API_KEY" in str(exc.value)


def test_setup_blocked_when_only_encrypted_rest_records():
    resolver = SecretResolver(EncryptedOnlyGateway(), _config(), env={})
    with pytest.raises(SetupBlocked) as exc:
        resolver.get_affinity_key()
    assert "AFFINITY_API_KEY" in str(exc.value)
