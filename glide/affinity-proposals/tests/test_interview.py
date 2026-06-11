"""Critical tests for the first-run interview (#967).

Scope is deliberately narrow:
  1. Config payload round-trips through the existing config consumers — if a
     key gets renamed, every downstream parser breaks silently.
  2. Password-item ranking prefers Affinity-ish titles — picking the wrong
     item would authenticate against someone else's CRM.
  3. Required answers reject blanks — silent empties become cryptic runtime
     errors for a non-engineer operator.
  4. End-to-end session writes a complete config — guards against orchestration
     bugs (forgetting to ask a question, writing it to the wrong key).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.agent import AgentConfig
from scripts.email_send import EmailConfig
from scripts.interview import (
    HIDDEN_DEFAULTS,
    InterviewAborted,
    InterviewAnswers,
    InterviewIO,
    InterviewSession,
    build_config_payload,
    rank_password_items,
)
from scripts.seren_client import PublisherError
from scripts.secrets import SecretConfig


def _fully_populated_answers() -> InterviewAnswers:
    return InterviewAnswers(
        list_name="Glide Prospects",
        engaged_status="Engaged - 25%",
        proposal_status="Proposal - 50%",
        owner_emails=["cristin@glide.example"],
        vault_name="Glide Vault",
        affinity_item_title="affinity-api-key",
        sender_address="proposals@serendb.com",
        dry_run_to="cristin@glide.example",
        dry_run_cc=["reviewer@glide.example"],
        live_cc=["manager@glide.example"],
        sharepoint_folder="Glide Proposals",
    )


def test_build_config_payload_includes_hidden_defaults_and_roundtrips() -> None:
    payload = build_config_payload(_fully_populated_answers())

    assert payload["dry_run"] is True
    assert payload["live_mode"] is False
    assert payload["serendb"] == HIDDEN_DEFAULTS["serendb"]
    assert payload["extract"]["model"] == HIDDEN_DEFAULTS["extract"]["model"]
    assert payload["secrets"]["affinity_env_var"] == "AFFINITY_API_KEY"

    agent = AgentConfig.from_mapping(payload)
    assert agent.dry_run is True
    assert agent.live_mode is False
    assert agent.sender_address == "proposals@serendb.com"
    assert agent.dry_run_to == "cristin@glide.example"
    assert agent.dry_run_cc == ["reviewer@glide.example"]
    assert agent.live_cc == ["manager@glide.example"]

    secrets = SecretConfig.from_mapping(payload["secrets"])
    assert secrets.vault_name == "Glide Vault"
    assert secrets.affinity_item_title == "affinity-api-key"
    assert secrets.affinity_env_var == "AFFINITY_API_KEY"

    email = EmailConfig.from_mapping(payload["email"])
    assert email.dry_run_to == "cristin@glide.example"
    assert email.live_cc == ["manager@glide.example"]


def test_rank_password_items_prefers_affinity_titles() -> None:
    items = [
        {"item_id": "x", "title": "stripe-secret"},
        {"item_id": "y", "title": "Affinity API Key"},
        {"item_id": "z", "title": "github-token"},
        {"item_id": "w", "title": "crm-api"},
    ]
    ranked = rank_password_items(items)
    assert ranked[0]["item_id"] == "y"
    assert ranked[1]["item_id"] == "w"
    assert {item["item_id"] for item in ranked[2:]} == {"x", "z"}


class _ScriptedIO:
    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self.writes: list[str] = []

    def ask(self, prompt: str) -> str:
        self.writes.append(prompt)
        return self._answers.pop(0)

    def write(self, message: str) -> None:
        self.writes.append(message)


def test_required_answer_rejects_blank_input_and_reprompts() -> None:
    io = _ScriptedIO(["", "   ", "Glide Prospects"])
    session = InterviewSession(
        io=InterviewIO(ask=io.ask, write=io.write),
        gateway=None,
        affinity_factory=lambda key: None,
        outlook_preflight=lambda address: None,
        sharepoint_preflight=lambda folder: None,
    )
    assert session._ask_required("list name? ", "list name") == "Glide Prospects"
    assert sum(1 for w in io.writes if "can't be empty" in w) == 2


def test_proposal_status_rejects_engaged_status_collision() -> None:
    io = _ScriptedIO(["Proposal - 50%", "Sent - 75%"])
    session = InterviewSession(
        io=InterviewIO(ask=io.ask, write=io.write),
        gateway=None,
        affinity_factory=lambda key: None,
        outlook_preflight=lambda address: None,
        sharepoint_preflight=lambda folder: None,
    )
    session.answers.engaged_status = "Proposal - 50%"

    session._ask_proposal_status()

    assert session.answers.proposal_status == "Sent - 75%"
    assert "can't be the same status" in "".join(io.writes)


class _StubGateway:
    """Minimal Passwords + Affinity stand-in for the end-to-end test.

    Returns deterministic vault/item shapes. No assertions about call order —
    only the final config matters.
    """

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def call_tool(self, publisher: str, tool: str, args: dict[str, Any] | None = None) -> Any:
        args = args or {}
        if tool == "passwords_vaults_list":
            return [{"vault_id": "v1", "name": "Glide Vault"}]
        if tool == "passwords_items_list":
            return [{"item_id": "i1", "title": "affinity-api-key"}]
        if tool == "passwords_item_get":
            return {"item": {"primary_value": "live-affinity-key"}}
        if tool == "passwords_item_create":
            created = {"item_id": "i-new", "title": args.get("title", "")}
            self.created.append(args)
            return {"item": created}
        raise AssertionError(f"unexpected tool {tool}")


class _StubAffinity:
    def __init__(self, key: str) -> None:
        self.key = key

    def lists(self) -> list[dict[str, Any]]:
        return [
            {"id": 1, "name": "Glide Prospects"},
            {"id": 2, "name": "Closed Deals"},
        ]


def test_full_session_writes_config_with_all_operator_answers(tmp_path: Path) -> None:
    scripted = [
        "Glide Prospects",          # 1. list name
        "Engaged - 25%",            # 2. engaged status
        "Proposal - 50%",           # 3. proposal status
        "cristin@glide.example",    # 4. owner email
        "y",                        # 5. confirm vault auto-select
        "y",                        # 5b. confirm matched password item
        "y",                        # 6. confirm Outlook mailbox
        "proposals@serendb.com",    # 6b. sender address
        "cristin@glide.example",    # 7. dry-run recipient
        "reviewer@glide.example",   # 8. dry-run cc
        "manager@glide.example",    # 9. live cc
        "",                         # 10. sharepoint folder (default)
        "y",                        # closing confirmation
    ]
    io = _ScriptedIO(scripted)
    gateway = _StubGateway()
    session = InterviewSession(
        io=InterviewIO(ask=io.ask, write=io.write),
        gateway=gateway,
        affinity_factory=_StubAffinity,
        outlook_preflight=lambda address: None,
        sharepoint_preflight=lambda folder: None,
    )

    answers = session.run()
    target = tmp_path / "config.json"
    session.write_to(target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["affinity"]["list_name"] == "Glide Prospects"
    assert payload["affinity"]["engaged_status"] == "Engaged - 25%"
    assert payload["affinity"]["proposal_status"] == "Proposal - 50%"
    assert payload["affinity"]["owner_emails"] == ["cristin@glide.example"]
    assert payload["secrets"]["vault_name"] == "Glide Vault"
    assert payload["secrets"]["affinity_item_title"] == "affinity-api-key"
    assert payload["email"]["sender_address"] == "proposals@serendb.com"
    assert payload["email"]["dry_run_to"] == "cristin@glide.example"
    assert payload["email"]["dry_run_cc"] == ["reviewer@glide.example"]
    assert payload["email"]["live_cc"] == ["manager@glide.example"]
    assert payload["sharepoint"]["folder_name"] == "AI Proposals"
    # Hidden infra defaults written for her — never asked.
    assert payload["dry_run"] is True
    assert payload["live_mode"] is False
    assert payload["serendb"] == HIDDEN_DEFAULTS["serendb"]
    # No item was created — she had a matching one.
    assert gateway.created == []
    assert answers.affinity_item_title == "affinity-api-key"


def test_setup_blocked_when_no_vaults_present() -> None:
    class NoVaults:
        def call_tool(self, publisher: str, tool: str, args: dict[str, Any] | None = None) -> Any:
            if tool == "passwords_vaults_list":
                return []
            raise AssertionError(tool)

    io = _ScriptedIO([
        "Glide Prospects",
        "Engaged - 25%",
        "Proposal - 50%",
        "cristin@glide.example",
    ])
    session = InterviewSession(
        io=InterviewIO(ask=io.ask, write=io.write),
        gateway=NoVaults(),
        affinity_factory=_StubAffinity,
        outlook_preflight=lambda address: None,
        sharepoint_preflight=lambda folder: None,
    )
    with pytest.raises(InterviewAborted) as exc:
        session.run()
    assert "Seren Passwords" in str(exc.value)


def test_passwords_failure_uses_env_first_fallback_without_chat_secret(tmp_path: Path) -> None:
    class PasswordsDown:
        def call_tool(self, publisher: str, tool: str, args: dict[str, Any] | None = None) -> Any:
            if tool == "passwords_vaults_list":
                raise PublisherError(503, "upstream service unavailable")
            raise AssertionError(tool)

    env_path = tmp_path / ".env"
    env_path.write_text("AFFINITY_API_KEY=env-affinity-key\n", encoding="utf-8")
    io = _ScriptedIO([
        "Glide Prospects",
        "Engaged - 25%",
        "Proposal - 50%",
        "cristin@glide.example",
        "stored",
        "y",
        "proposals@serendb.com",
        "cristin@glide.example",
        "",
        "manager@glide.example",
        "",
        "y",
    ])
    seen_keys: list[str] = []

    class EnvAffinity(_StubAffinity):
        def __init__(self, key: str) -> None:
            super().__init__(key)
            seen_keys.append(key)

    session = InterviewSession(
        io=InterviewIO(ask=io.ask, write=io.write),
        gateway=PasswordsDown(),
        affinity_factory=EnvAffinity,
        outlook_preflight=lambda address: None,
        sharepoint_preflight=lambda folder: None,
        env_path=env_path,
    )

    session.run()
    payload = build_config_payload(session.answers)

    assert seen_keys == ["env-affinity-key"]
    assert payload["secrets"]["vault_name"] is None
    assert payload["secrets"]["affinity_item_title"] is None
    assert "env-first fallback" in "".join(io.writes)
    assert "AFFINITY_API_KEY" in "".join(io.writes)


def test_env_key_gets_vault_recovered_reminder_and_can_stay_deferred(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("AFFINITY_API_KEY=env-affinity-key\n", encoding="utf-8")
    io = _ScriptedIO([
        "Glide Prospects",
        "Engaged - 25%",
        "Proposal - 50%",
        "cristin@glide.example",
        "n",
        "y",
        "proposals@serendb.com",
        "cristin@glide.example",
        "",
        "manager@glide.example",
        "",
        "y",
    ])
    session = InterviewSession(
        io=InterviewIO(ask=io.ask, write=io.write),
        gateway=_StubGateway(),
        affinity_factory=_StubAffinity,
        outlook_preflight=lambda address: None,
        sharepoint_preflight=lambda folder: None,
        env_path=env_path,
    )

    session.run()

    assert session.answers.vault_name is None
    assert session.answers.affinity_item_title is None
    assert "vault is back up" in "".join(io.writes)
