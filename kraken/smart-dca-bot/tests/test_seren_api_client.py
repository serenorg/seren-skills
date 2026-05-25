from __future__ import annotations

import pytest

from seren_api_client import SerenAPIError, SerenAPIKeyManager


def test_missing_key_returns_manual_setup_message(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SEREN_BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.delenv("SEREN_AUTH_TOKEN", raising=False)
    manager = SerenAPIKeyManager(env_file=str(tmp_path / ".env"))

    with pytest.raises(SerenAPIError) as exc:
        manager.ensure_api_key()

    message = str(exc.value)
    assert "SEREN_API_KEY is required" in message
    assert "https://docs.serendb.com/skills.md" in message


def test_desktop_api_key_alias_is_accepted(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "desktop-token")
    manager = SerenAPIKeyManager(env_file=str(tmp_path / ".env"))
    monkeypatch.setattr(manager, "validate_existing_key", lambda api_key: True)

    assert manager.ensure_api_key() == "desktop-token"
    assert manager.ensure_api_key() == "desktop-token"
    assert manager.env_file.exists() is False


def test_auto_register_without_bootstrap_token_does_not_call_dead_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SEREN_BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.delenv("SEREN_AUTH_TOKEN", raising=False)
    manager = SerenAPIKeyManager(env_file=str(tmp_path / ".env"))

    with pytest.raises(SerenAPIError, match="SEREN_API_KEY is required"):
        manager.ensure_api_key(auto_register=True)
