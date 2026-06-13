"""First-run conversational setup for non-engineer operators (#967).

The legacy setup flow asked the operator to hand-edit ``config.json`` from a
placeholder example. The actual operator is a sales contractor, not an
engineer, and the example file mixed infra fields (SerenDB, extract model,
env-var names) with the operator-facing fields she actually needs to choose.

This module collects only the operator-facing answers, bakes the infra
defaults in for her, and writes a ready-to-run ``config.json``. The existing
``scripts/secrets.py`` env-first resolver picks up the Affinity API key on
every subsequent run via the Seren Passwords path, so she never types or
pastes a key into the terminal.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from scripts.extract import DEFAULT_MODEL
from scripts.seren_client import PublisherError


HIDDEN_DEFAULTS: dict[str, Any] = {
    "dry_run": True,
    "live_mode": False,
    "extract": {"model": DEFAULT_MODEL},
    "secrets_env_var": "AFFINITY_API_KEY",
    "serendb": {
        "project": "glide-affinity-proposals",
        "database": "glide_affinity_proposals",
    },
}

DEFAULT_SHAREPOINT_FOLDER = "AI Proposals"
# Cristin's spec (#980): live mode CCs the manager (Mark) and Cristin.
DEFAULT_LIVE_CC = ("mark@glideplatform.com", "cristin@glideplatform.com")

_AFFINITY_KEY_HINTS = ("affinity", "api", "key", "crm")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InterviewAborted(RuntimeError):
    """Raised when an upstream prerequisite blocks setup (e.g., no vaults)."""


@dataclass
class InterviewAnswers:
    list_name: str = ""
    engaged_status: str = ""
    proposal_status: str = ""
    owner_emails: list[str] = field(default_factory=list)
    vault_name: str | None = ""
    affinity_item_title: str | None = ""
    sender_address: str = ""
    dry_run_to: str = ""
    dry_run_cc: list[str] = field(default_factory=list)
    live_cc: list[str] = field(default_factory=list)
    sharepoint_folder: str = DEFAULT_SHAREPOINT_FOLDER


@dataclass
class InterviewIO:
    """I/O surface, injected so tests can script prompts and capture writes."""

    ask: Callable[[str], str]
    write: Callable[[str], None]


def build_config_payload(answers: InterviewAnswers) -> dict[str, Any]:
    return {
        "dry_run": HIDDEN_DEFAULTS["dry_run"],
        "live_mode": HIDDEN_DEFAULTS["live_mode"],
        "affinity": {
            "list_name": answers.list_name,
            "engaged_status": answers.engaged_status,
            "proposal_status": answers.proposal_status,
            "owner_emails": list(answers.owner_emails),
        },
        "secrets": {
            "vault_name": answers.vault_name,
            "affinity_item_title": answers.affinity_item_title,
            "affinity_env_var": HIDDEN_DEFAULTS["secrets_env_var"],
        },
        "extract": dict(HIDDEN_DEFAULTS["extract"]),
        "email": {
            "sender_address": answers.sender_address,
            "dry_run_to": answers.dry_run_to,
            "dry_run_cc": list(answers.dry_run_cc),
            "live_cc": list(answers.live_cc),
        },
        "sharepoint": {"folder_name": answers.sharepoint_folder or DEFAULT_SHAREPOINT_FOLDER},
        "serendb": dict(HIDDEN_DEFAULTS["serendb"]),
    }


def score_password_item_title(title: str) -> int:
    lowered = (title or "").lower()
    return sum(1 for hint in _AFFINITY_KEY_HINTS if hint in lowered)


def rank_password_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(items))
    indexed.sort(
        key=lambda pair: (-score_password_item_title(pair[1].get("title", "")), pair[0])
    )
    return [item for _, item in indexed]


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))


def parse_email_list(value: str) -> list[str]:
    if not value or not value.strip():
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _passwords_list(response: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        value = response.get(key) or response.get("data") or []
    else:
        value = response
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


class InterviewSession:
    """Runs the conversational interview and writes config.json.

    The provider callables are injected so the session is testable end-to-end
    without touching live Affinity/Outlook/SharePoint accounts. Real callers
    in ``scripts/agent.py`` pass the live clients.
    """

    def __init__(
        self,
        *,
        io: InterviewIO,
        gateway: Any,
        affinity_factory: Callable[[str], Any],
        outlook_preflight: Callable[[str], None],
        sharepoint_preflight: Callable[[str], None],
        env_path: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.io = io
        self.gateway = gateway
        self.affinity_factory = affinity_factory
        self.outlook_preflight = outlook_preflight
        self.sharepoint_preflight = sharepoint_preflight
        self.env_path = env_path or Path(__file__).resolve().parent.parent / ".env"
        self.env = env if env is not None else os.environ
        self.answers = InterviewAnswers()

    def run(self) -> InterviewAnswers:
        self._intro()
        self._ask_list()
        self._ask_engaged_status()
        self._ask_proposal_status()
        self._ask_owner_emails()
        self._ask_passwords_item()
        self._ask_sender()
        self._ask_dry_run_to()
        self._ask_dry_run_cc()
        self._ask_live_cc()
        self._ask_sharepoint_folder()
        self._final_confirm()
        return self.answers

    def write_to(self, path: Path) -> None:
        payload = build_config_payload(self.answers)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _intro(self) -> None:
        self.io.write(
            "Glide Affinity Proposals — first-run setup.\n"
            "I'll ask a few questions about your CRM, mailbox, and where to send "
            "dry-run reviews. You can re-run setup any time with --setup.\n\n"
        )

    def _ask_required(self, prompt: str, label: str) -> str:
        while True:
            value = self.io.ask(prompt).strip()
            if value:
                return value
            self.io.write(f"  {label} can't be empty — please answer.\n")

    def _ask_required_email(self, prompt: str, label: str) -> str:
        while True:
            value = self._ask_required(prompt, label)
            if is_valid_email(value):
                return value
            self.io.write(f"  That doesn't look like a valid email — please try again.\n")

    def _ask_list(self) -> None:
        self.answers.list_name = self._ask_required(
            "1) Which Affinity list are we scanning? ", "list name"
        )

    def _ask_engaged_status(self) -> None:
        self.answers.engaged_status = self._ask_required(
            "2) What status marks a prospect as ready for a proposal? "
            "(e.g. 'Engaged - 25%') ",
            "engaged status",
        )

    def _ask_proposal_status(self) -> None:
        while True:
            value = self._ask_required(
                "3) What status should we move them to after the proposal is sent? "
                "(e.g. 'Proposal - 50%') ",
                "proposal status",
            )
            if value.strip().lower() != self.answers.engaged_status.strip().lower():
                self.answers.proposal_status = value
                return
            self.io.write(
                "  Q2 (engaged) and Q3 (post-send) describe two different "
                "points in the funnel. They can't be the same status. Engaged "
                "is \"ready for proposal\"; post-send is what you move them to "
                "after the PDF goes out. What's the post-send status?\n"
            )

    def _ask_owner_emails(self) -> None:
        raw = self.io.ask(
            "4) Whose prospects should we work on? (your email, or 'everyone') "
        ).strip()
        if not raw or raw.lower() == "everyone":
            self.answers.owner_emails = []
            self.io.write("   -> all owners in the list\n")
            return
        emails = parse_email_list(raw)
        bad = [email for email in emails if not is_valid_email(email)]
        if bad or not emails:
            self.io.write("  Couldn't parse those as emails — try again.\n")
            return self._ask_owner_emails()
        self.answers.owner_emails = emails

    def _ask_passwords_item(self) -> None:
        if self._affinity_key_from_env() and self._passwords_available():
            answer = self.io.ask(
                "5) Affinity key is on `.env` — vault is back up; want to migrate? "
                "(y/n) "
            ).strip().lower()
            if answer not in ("y", "yes", ""):
                self._use_env_first_key()
                return
        try:
            vault_id, vault_name = self._select_password_vault()
            self.answers.vault_name = vault_name
            item = self._select_password_item(vault_id)
            if item is None:
                item = self._create_password_item(vault_id)
            self.answers.affinity_item_title = item["title"]

            api_key = self._reveal_password_item(vault_id, item["item_id"])
            self._verify_affinity_key(api_key, source="Seren Passwords")
        except PublisherError as exc:
            self._ask_env_first_fallback(exc)

    def _select_password_vault(self) -> tuple[str, str]:
        vaults = _passwords_list(
            self.gateway.call_tool("seren-passwords", "passwords_vaults_list", {}),
            "vaults",
        )
        if not vaults:
            raise InterviewAborted(
                "No vaults found in Seren Passwords. Open Seren Passwords and "
                "create a vault before running setup."
            )
        if len(vaults) == 1:
            name = str(vaults[0].get("name") or "")
            self.io.write(f"5) Using your Seren Passwords vault: {name}\n")
            ok = self.io.ask("   Is that the right vault? (y/n) ").strip().lower()
            if ok not in ("y", "yes", ""):
                raise InterviewAborted(
                    "Aborted — switch to the right vault in Seren Passwords and re-run setup."
                )
            return str(vaults[0].get("vault_id") or vaults[0].get("id")), name
        self.io.write("5) Which vault has your Affinity API key?\n")
        for index, vault in enumerate(vaults, start=1):
            self.io.write(f"   {index}) {vault.get('name')}\n")
        while True:
            answer = self.io.ask("   Pick one (number): ").strip()
            if answer.isdigit():
                index = int(answer)
                if 1 <= index <= len(vaults):
                    vault = vaults[index - 1]
                    return (
                        str(vault.get("vault_id") or vault.get("id")),
                        str(vault.get("name") or ""),
                    )
            self.io.write("   That's not one of the choices — try again.\n")

    def _select_password_item(self, vault_id: str) -> dict[str, Any] | None:
        items = _passwords_list(
            self.gateway.call_tool(
                "seren-passwords", "passwords_items_list", {"vault_id": vault_id}
            ),
            "items",
        )
        ranked = rank_password_items(items)
        top = [
            item for item in ranked if score_password_item_title(item.get("title", "")) > 0
        ][:3]
        if not top:
            self.io.write(
                "   No Affinity-looking items in this vault — I'll add one for you.\n"
            )
            return None
        if len(top) == 1:
            title = top[0].get("title", "")
            self.io.write(f"   I found one: {title}\n")
            answer = self.io.ask("   Use this one? (y/n) ").strip().lower()
            if answer in ("y", "yes", ""):
                return {"item_id": str(top[0].get("item_id") or top[0].get("id")), "title": title}
            return None
        self.io.write("   I found these matches:\n")
        for index, item in enumerate(top, start=1):
            self.io.write(f"     {index}) {item.get('title')}\n")
        self.io.write(f"     {len(top) + 1}) None of these — I'll add one\n")
        while True:
            answer = self.io.ask("   Pick one (number): ").strip()
            if answer.isdigit():
                pick = int(answer)
                if 1 <= pick <= len(top):
                    item = top[pick - 1]
                    return {
                        "item_id": str(item.get("item_id") or item.get("id")),
                        "title": str(item.get("title") or ""),
                    }
                if pick == len(top) + 1:
                    return None
            self.io.write("   That's not one of the choices — try again.\n")

    def _create_password_item(self, vault_id: str) -> dict[str, Any]:
        title = "affinity-api-key"
        secret = self._ask_required(
            "   Paste your Affinity API key once (it goes straight to Seren Passwords): ",
            "Affinity API key",
        )
        response = self.gateway.call_tool(
            "seren-passwords",
            "passwords_item_create",
            {"vault_id": vault_id, "title": title, "primary_value": secret},
        )
        item = response.get("item") if isinstance(response, dict) else None
        item_id = (
            str(item.get("item_id") or item.get("id"))
            if isinstance(item, dict)
            else ""
        )
        if not item_id:
            raise InterviewAborted(
                "Seren Passwords did not return an item id after create — please re-run setup."
            )
        return {"item_id": item_id, "title": title}

    def _reveal_password_item(self, vault_id: str, item_id: str) -> str:
        response = self.gateway.call_tool(
            "seren-passwords",
            "passwords_item_get",
            {"vault_id": vault_id, "item_id": item_id, "reveal": True},
        )
        item = response.get("item") if isinstance(response, dict) else response
        if not isinstance(item, dict):
            raise InterviewAborted("Seren Passwords item response was not an object")
        value = item.get("primary_value") or item.get("password") or item.get("value")
        if not value:
            fields = item.get("fields") or {}
            if isinstance(fields, dict):
                value = (
                    fields.get("primary_value")
                    or fields.get("password")
                    or fields.get("value")
                )
        if not value:
            raise InterviewAborted(
                "Seren Passwords item had no primary value — re-create it with the key in 'primary_value'."
            )
        return str(value).strip()

    def _ask_env_first_fallback(self, exc: PublisherError) -> None:
        self.io.write(
            f"5) Seren Passwords is unavailable ({exc.status}). The skill reads "
            "`AFFINITY_API_KEY` from the environment before it touches Passwords; "
            "this is the documented headless env-first fallback.\n"
            f"   Store this line in {self.env_path} with file mode 0600:\n"
            "   AFFINITY_API_KEY=<your Affinity API key>\n"
            "   Do not paste the key into chat. Reply `stored` after the file is saved.\n"
        )
        while True:
            answer = self.io.ask("   Stored outside chat? (type 'stored') ").strip().lower()
            if answer == "stored":
                try:
                    self._use_env_first_key()
                    return
                except InterviewAborted as abort:
                    self.io.write(f"   {abort}\n")
            else:
                self.io.write("   Please store it outside chat, then reply `stored`.\n")

    def _passwords_available(self) -> bool:
        try:
            vaults = _passwords_list(
                self.gateway.call_tool("seren-passwords", "passwords_vaults_list", {}),
                "vaults",
            )
        except PublisherError:
            return False
        return bool(vaults)

    def _use_env_first_key(self) -> None:
        api_key = self._affinity_key_from_env()
        if not api_key:
            raise InterviewAborted(
                f"{HIDDEN_DEFAULTS['secrets_env_var']} was not found in the "
                f"environment or {self.env_path}."
            )
        self.answers.vault_name = None
        self.answers.affinity_item_title = None
        self._verify_affinity_key(api_key, source=HIDDEN_DEFAULTS["secrets_env_var"])

    def _affinity_key_from_env(self) -> str:
        env_var = HIDDEN_DEFAULTS["secrets_env_var"]
        value = self.env.get(env_var)
        if value and value.strip():
            return value.strip()
        if not self.env_path.exists():
            return ""
        for line in self.env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{env_var}="):
                return line.partition("=")[2].strip()
        return ""

    def _verify_affinity_key(self, api_key: str, *, source: str) -> None:
        try:
            client = self.affinity_factory(api_key)
            client.lists()
        except PublisherError as exc:
            raise InterviewAborted(
                f"Affinity rejected the API key from {source} ({exc}). "
                "Check the value and re-run setup."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - any failure is a setup blocker
            raise InterviewAborted(
                f"Couldn't authenticate against Affinity with the key from {source}: {exc}"
            ) from exc

    def _ask_sender(self) -> None:
        self.io.write(
            "6) The proposal email will come from the Outlook mailbox connected to "
            "the Seren tenant.\n"
        )
        confirm = self.io.ask("   Is your Outlook mailbox already connected? (y/n) ").strip().lower()
        if confirm not in ("y", "yes", ""):
            raise InterviewAborted(
                "Connect your Outlook mailbox to the microsoft-outlook publisher first, then re-run setup."
            )
        address = self._ask_required_email(
            "   What's the From address on that mailbox? ", "sender address"
        )
        self.answers.sender_address = address
        try:
            self.outlook_preflight(address)
        except Exception as exc:  # noqa: BLE001
            raise InterviewAborted(
                f"Outlook preflight failed: {exc}. Reconnect the mailbox and re-run setup."
            ) from exc

    def _ask_dry_run_to(self) -> None:
        self.answers.dry_run_to = self._ask_required_email(
            "7) Where should dry-run review emails go? (your email) ", "dry-run recipient"
        )

    def _ask_dry_run_cc(self) -> None:
        raw = self.io.ask("8) Anyone else CC'd on dry-runs? (comma-separated, or blank) ").strip()
        emails = parse_email_list(raw)
        if any(not is_valid_email(email) for email in emails):
            self.io.write("  Couldn't parse those as emails — try again.\n")
            return self._ask_dry_run_cc()
        self.answers.dry_run_cc = emails

    def _ask_live_cc(self) -> None:
        default = ", ".join(DEFAULT_LIVE_CC)
        raw = self.io.ask(
            "9) When we go live, who's CC'd? (manager + anyone else, "
            f"comma-separated) [default: {default}] "
        ).strip()
        if not raw:
            self.answers.live_cc = list(DEFAULT_LIVE_CC)
            return
        emails = parse_email_list(raw)
        if not emails or any(not is_valid_email(email) for email in emails):
            self.io.write("  Couldn't parse those as emails — try again.\n")
            return self._ask_live_cc()
        self.answers.live_cc = emails

    def _ask_sharepoint_folder(self) -> None:
        raw = self.io.ask(
            f"10) SharePoint archive folder? (press enter for '{DEFAULT_SHAREPOINT_FOLDER}') "
        ).strip()
        folder = raw or DEFAULT_SHAREPOINT_FOLDER
        self.answers.sharepoint_folder = folder
        try:
            self.sharepoint_preflight(folder)
        except Exception as exc:  # noqa: BLE001
            raise InterviewAborted(
                f"SharePoint preflight failed: {exc}. Connect the render account and re-run setup."
            ) from exc

    def _final_confirm(self) -> None:
        owners = (
            ", ".join(self.answers.owner_emails)
            if self.answers.owner_emails
            else "every owner in the list"
        )
        dry_cc = (
            ", ".join(self.answers.dry_run_cc) if self.answers.dry_run_cc else "(none)"
        )
        self.io.write(
            "\nHere's what the next dry-run will do:\n"
            f"  - Scan the '{self.answers.list_name}' list in Affinity\n"
            f"  - For prospects owned by {owners}\n"
            f"  - With status '{self.answers.engaged_status}' and no prior proposal note\n"
            "  - Render a proposal PDF from the most recent meeting note\n"
            f"  - Email the review to {self.answers.dry_run_to} (CC {dry_cc}) "
            f"from {self.answers.sender_address}\n"
            f"  - Archive the PDF in SharePoint under '{self.answers.sharepoint_folder}'\n\n"
        )
        answer = self.io.ask("Ready to save this and run? (y/n) ").strip().lower()
        if answer not in ("y", "yes", ""):
            raise InterviewAborted("Aborted at confirmation — nothing was saved.")
