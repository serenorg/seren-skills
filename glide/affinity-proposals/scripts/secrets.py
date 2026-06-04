from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.seren_client import PublisherError


LOGGER = logging.getLogger(__name__)


class SetupBlocked(RuntimeError):
    """Raised when Passwords setup is incomplete for this runtime."""


@dataclass
class SecretConfig:
    vault_name: str
    affinity_item_title: str
    microsoft_login_item_title: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SecretConfig":
        return cls(
            vault_name=str(data["vault_name"]),
            affinity_item_title=str(data["affinity_item_title"]),
            microsoft_login_item_title=str(data["microsoft_login_item_title"]),
        )


class SecretResolver:
    def __init__(self, gateway: Any, config: SecretConfig) -> None:
        self.gateway = gateway
        self.config = config
        self._vault_id: str | None = None
        self._items: list[dict[str, Any]] | None = None
        self._affinity_key: str | None = None
        self._ms_login: dict[str, Any] | None = None

    def _vaults(self) -> list[dict[str, Any]]:
        try:
            response = self.gateway.call_tool(
                "seren-passwords",
                "passwords_vaults_list",
                {},
            )
        except PublisherError:
            response = self.gateway.call_tool("seren-passwords", "get_vaults", {})
        return _as_list(response, "vaults")

    def _vault_id_by_name(self) -> str:
        if self._vault_id:
            return self._vault_id
        saw_encrypted_only = False
        for vault in self._vaults():
            if vault.get("name") == self.config.vault_name:
                self._vault_id = str(vault.get("vault_id") or vault.get("id"))
                return self._vault_id
            if vault.get("name_ciphertext") and not vault.get("name"):
                saw_encrypted_only = True
        if saw_encrypted_only:
            raise SetupBlocked(
                "This runtime can reach only encrypted Seren Passwords REST records. "
                "Expose the hosted Seren Passwords tools "
                "(passwords_vaults_list, passwords_items_list, passwords_item_get) "
                "before running this skill."
            )
        raise LookupError("Configured Seren Passwords vault was not found")

    def _list_items(self) -> list[dict[str, Any]]:
        if self._items is not None:
            return self._items
        vault_id = self._vault_id_by_name()
        try:
            response = self.gateway.call_tool(
                "seren-passwords",
                "passwords_items_list",
                {"vault_id": vault_id},
            )
        except PublisherError:
            response = self.gateway.call_tool(
                "seren-passwords",
                "get_vaults_by_vault_id_items",
                {"vault_id": vault_id},
            )
        self._items = _as_list(response, "items")
        return self._items

    def _item_id_by_title(self, title: str) -> str:
        for item in self._list_items():
            if item.get("title") == title or item.get("name") == title:
                return str(item.get("item_id") or item.get("id"))
        raise LookupError("Configured Seren Passwords item was not found")

    def _get_item(self, title: str) -> dict[str, Any]:
        vault_id = self._vault_id_by_name()
        item_id = self._item_id_by_title(title)
        try:
            response = self.gateway.call_tool(
                "seren-passwords",
                "passwords_item_get",
                {"vault_id": vault_id, "item_id": item_id, "reveal": True},
            )
        except PublisherError:
            response = self.gateway.call_tool(
                "seren-passwords",
                "get_vaults_by_vault_id_items_by_item_id",
                {"vault_id": vault_id, "item_id": item_id, "reveal": True},
            )
        item = response.get("item") if isinstance(response, dict) else response
        if not isinstance(item, dict):
            raise RuntimeError("Seren Passwords item response was not an object")
        return item

    def get_affinity_key(self) -> str:
        if self._affinity_key is not None:
            return self._affinity_key
        item = self._get_item(self.config.affinity_item_title)
        value = item.get("primary_value") or item.get("password") or item.get("value")
        if not value:
            fields = item.get("fields") or {}
            value = fields.get("primary_value") or fields.get("password") or fields.get("value")
        if not value:
            raise RuntimeError("Affinity secret item did not contain a primary value")
        self._affinity_key = str(value).strip()
        LOGGER.info("Resolved Affinity key from Seren Passwords (len=%s)", len(self._affinity_key))
        return self._affinity_key

    def get_ms_login(self) -> dict[str, Any]:
        if self._ms_login is not None:
            return dict(self._ms_login)
        item = self._get_item(self.config.microsoft_login_item_title)
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else item
        login = {
            "username": fields.get("username") or fields.get("email"),
            "password": fields.get("password"),
            "totp": fields.get("totp") or fields.get("otp") or fields.get("totp_secret"),
        }
        if not login["username"] or not login["password"]:
            raise RuntimeError("Microsoft login item missing username or password")
        self._ms_login = login
        username = str(login["username"])
        masked = username[:2] + "***" + username[-4:] if len(username) > 6 else "***"
        LOGGER.info("Resolved Microsoft login from Seren Passwords (user=%s)", masked)
        return dict(login)


def _as_list(response: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        value = response.get(key) or response.get("data") or []
    else:
        value = response
    if not isinstance(value, list):
        raise RuntimeError(f"Expected {key} list from Seren Passwords")
    return [item for item in value if isinstance(item, dict)]


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args()
    if not args.selfcheck:
        parser.error("only --selfcheck is supported")

    from scripts.seren_client import GatewayClient

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = _load_config(Path(args.config))
    resolver = SecretResolver(
        GatewayClient.from_env(),
        SecretConfig.from_mapping(config.get("secrets", {})),
    )
    try:
        key = resolver.get_affinity_key()
        login = resolver.get_ms_login()
    except SetupBlocked as exc:
        print(f"setup-blocked: {exc}")
        return 2
    user = str(login["username"])
    masked = user[:2] + "***" + user[-4:] if len(user) > 6 else "***"
    print(f"affinity-crm: OK (len={len(key)})")
    print(f"ms-login: OK (user={masked})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
