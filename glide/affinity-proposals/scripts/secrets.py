from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.seren_client import PublisherError


LOGGER = logging.getLogger(__name__)


class SetupBlocked(RuntimeError):
    """Raised when secret setup is incomplete for this runtime."""


def _missing_secret_message(env_var: str) -> str:
    return (
        f"Affinity API key is not available. Set {env_var} in the environment "
        "or the skill's .env / cloud secret store, or grant the hosted Seren "
        "Passwords tools (passwords_vaults_list, passwords_items_list, "
        "passwords_item_get) that return plaintext after an access grant."
    )


@dataclass
class SecretConfig:
    vault_name: str
    affinity_item_title: str
    affinity_env_var: str = "AFFINITY_API_KEY"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SecretConfig":
        return cls(
            vault_name=str(data.get("vault_name") or ""),
            affinity_item_title=str(data.get("affinity_item_title") or ""),
            affinity_env_var=str(data.get("affinity_env_var", "AFFINITY_API_KEY")),
        )


class SecretResolver:
    """Resolve the Affinity API key, env-first.

    Order:
      1. Environment / `.env` / cloud secret store (`AFFINITY_API_KEY`).
         This is the cloud-cron path — the only one that works headless,
         mirroring how the sibling `pk-lead-intelligence` skill injects
         secrets (issue #865).
      2. Hosted Seren Passwords MCP tools (desktop / post-grant). These
         return plaintext only when present; the pure-HTTP REST surface
         is end-to-end encrypted and cannot decrypt (issue #861).

    `gateway` may be `None` (cloud with no Passwords MCP). `env` is
    injectable for tests; it defaults to `os.environ`.
    """

    def __init__(
        self,
        gateway: Any,
        config: SecretConfig,
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        self.gateway = gateway
        self.config = config
        self._env = env if env is not None else os.environ
        self._vault_id: str | None = None
        self._items: list[dict[str, Any]] | None = None
        self._affinity_key: str | None = None

    def get_affinity_key(self) -> str:
        if self._affinity_key is not None:
            return self._affinity_key

        env_value = self._env.get(self.config.affinity_env_var)
        if env_value and env_value.strip():
            self._affinity_key = env_value.strip()
            LOGGER.info(
                "Resolved Affinity key from %s (len=%s)",
                self.config.affinity_env_var,
                len(self._affinity_key),
            )
            return self._affinity_key

        if self.gateway is not None:
            value = self._affinity_key_from_passwords()
            if value:
                self._affinity_key = value
                LOGGER.info(
                    "Resolved Affinity key from Seren Passwords (len=%s)",
                    len(self._affinity_key),
                )
                return self._affinity_key

        raise SetupBlocked(_missing_secret_message(self.config.affinity_env_var))

    # ---- Seren Passwords fallback ------------------------------------- #

    def _affinity_key_from_passwords(self) -> str | None:
        try:
            item = self._get_item(self.config.affinity_item_title)
        except SetupBlocked:
            raise
        except (PublisherError, LookupError):
            return None
        value = item.get("primary_value") or item.get("password") or item.get("value")
        if not value:
            fields = item.get("fields") or {}
            value = (
                fields.get("primary_value")
                or fields.get("password")
                or fields.get("value")
            )
        return str(value).strip() if value else None

    def _vaults(self) -> list[dict[str, Any]]:
        try:
            response = self.gateway.call_tool("seren-passwords", "passwords_vaults_list", {})
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
            raise SetupBlocked(_missing_secret_message(self.config.affinity_env_var))
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
    try:
        gateway = GatewayClient.from_env()
    except RuntimeError:
        gateway = None
    resolver = SecretResolver(
        gateway,
        SecretConfig.from_mapping(config.get("secrets", {})),
    )
    try:
        key = resolver.get_affinity_key()
    except SetupBlocked as exc:
        print(f"setup-blocked: {exc}")
        return 2
    print(f"affinity-crm: OK (len={len(key)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
