from __future__ import annotations

import pytest

from scripts.secrets import SecretConfig, SecretResolver, SetupBlocked
from scripts.seren_client import PublisherError


class EncryptedOnlyGateway:
    def call_tool(self, publisher, tool, tool_args=None):
        if tool in {"passwords_vaults_list", "passwords_items_list", "passwords_item_get"}:
            raise PublisherError(403, "Endpoint is not in the allowed endpoints list")
        if tool == "get_vaults":
            return {"data": [{"vault_id": "vault-1", "name_ciphertext": "encrypted"}]}
        raise AssertionError(tool)


def test_passwords_resolver_blocks_when_only_encrypted_rest_records_available():
    resolver = SecretResolver(
        EncryptedOnlyGateway(),
        SecretConfig(
            vault_name="Demo Vault",
            affinity_item_title="crm-key",
            microsoft_login_item_title="ms-login",
        ),
    )

    with pytest.raises(SetupBlocked) as exc:
        resolver.get_affinity_key()

    assert "hosted Seren Passwords tools" in str(exc.value)
