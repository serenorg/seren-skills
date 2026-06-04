from __future__ import annotations

import argparse
import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass
class Note:
    content: str
    created_at: str | None = None


@dataclass
class Prospect:
    prospect_id: str
    org_id: str
    name: str
    status: str
    owner_email: str
    contact_date: str
    notes: list[Note] = field(default_factory=list)
    status_field_value_id: str | None = None
    proposal_status_option_id: str | None = None


def _note_text(note: Note | dict[str, Any] | str) -> str:
    if isinstance(note, Note):
        return note.content or ""
    if isinstance(note, str):
        return note
    return str(note.get("content") or note.get("text") or "")


def looks_like_meeting_summary(note: Note | dict[str, Any] | str) -> bool:
    text = re.sub(r"\s+", " ", _note_text(note)).strip()
    if len(text) < 100:
        return False
    lowered = text.lower()
    activity_phrases = (
        "email sent",
        "left voicemail",
        "calendar invite",
        "followed up",
    )
    return not any(phrase in lowered and len(text) < 180 for phrase in activity_phrases)


def has_prior_proposal_note(notes: list[Note | dict[str, Any] | str]) -> bool:
    return any("proposal" in _note_text(note).lower() for note in notes)


def should_generate_proposal(
    *,
    status: str,
    notes: list[Note | dict[str, Any] | str],
    engaged_status: str = "Engaged - 25%",
) -> bool:
    if status != engaged_status:
        return False
    if has_prior_proposal_note(notes):
        return False
    return any(looks_like_meeting_summary(note) for note in notes)


class AffinityClient:
    """Small Affinity REST client.

    Affinity authenticates with HTTP Basic where the API key is the
    username and the password is blank.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.affinity.co",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Authorization": f"Basic {token}"},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def lists(self) -> list[dict[str, Any]]:
        return self._request("GET", "/lists")

    def find_list(self, name: str) -> dict[str, Any]:
        for item in self.lists():
            if item.get("name") == name:
                return item
        raise LookupError(f"Affinity list not found: {name}")

    def list_entries(self, list_id: str | int) -> list[dict[str, Any]]:
        return self._request("GET", f"/lists/{list_id}/list-entries")

    def list_fields(self, list_id: str | int) -> Any:
        return self._request("GET", f"/lists/{list_id}")

    def field_values(self, list_entry_id: str | int) -> list[dict[str, Any]]:
        return self._request("GET", "/field-values", params={"list_entry_id": list_entry_id})

    def notes(self, org_id: str | int) -> list[dict[str, Any]]:
        return self._request("GET", "/notes", params={"organization_id": org_id})

    def person(self, person_id: str | int) -> dict[str, Any]:
        return self._request("GET", f"/persons/{person_id}")

    def add_note(self, org_id: str | int, content: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/notes",
            json={"organization_id": org_id, "content": content},
        )

    def set_status(self, field_value_id: str | int, status_option_id: str | int) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/field-values/{field_value_id}",
            json={"value": status_option_id},
        )


class AffinityProspectSource:
    def __init__(
        self,
        client: AffinityClient,
        *,
        list_name: str,
        engaged_status: str = "Engaged - 25%",
        proposal_status: str = "Proposal - 50%",
        owner_emails: list[str] | str | None = None,
    ) -> None:
        self.client = client
        self.list_name = list_name
        self.engaged_status = engaged_status
        self.proposal_status = proposal_status
        self.owner_emails = _normalize_owner_emails(owner_emails)

    def _owner_allowed(self, owner_email: str) -> bool:
        if not self.owner_emails:
            return True
        return owner_email.strip().lower() in self.owner_emails

    def qualified_prospects(self) -> list[Prospect]:
        prospect_list = self.client.find_list(self.list_name)
        list_id = prospect_list["id"]
        fields_payload = self.client.list_fields(list_id)
        proposal_status_option_id = _find_status_option_id(
            fields_payload,
            self.proposal_status,
        )
        prospects: list[Prospect] = []
        for entry in self.client.list_entries(list_id):
            entry_id = entry.get("id") or entry.get("list_entry_id")
            if not entry_id:
                continue
            field_values = self.client.field_values(entry_id)
            status, status_field_value_id = _extract_status(field_values)
            org_id = _extract_org_id(entry)
            if not org_id:
                continue
            owner_email = _extract_owner_email(field_values)
            if not self._owner_allowed(owner_email):
                continue
            notes = [
                Note(
                    content=str(item.get("content") or item.get("text") or ""),
                    created_at=item.get("created_at") or item.get("date"),
                )
                for item in self.client.notes(org_id)
            ]
            if not should_generate_proposal(
                status=status,
                notes=notes,
                engaged_status=self.engaged_status,
            ):
                continue
            prospects.append(
                Prospect(
                    prospect_id=str(entry_id),
                    org_id=str(org_id),
                    name=_extract_name(entry),
                    status=status,
                    owner_email=owner_email,
                    contact_date=_latest_note_date(notes),
                    notes=notes,
                    status_field_value_id=status_field_value_id,
                    proposal_status_option_id=proposal_status_option_id,
                )
            )
        return prospects


def _extract_name(entry: dict[str, Any]) -> str:
    entity = entry.get("entity") if isinstance(entry.get("entity"), dict) else {}
    return str(
        entry.get("name")
        or entity.get("name")
        or entry.get("organization_name")
        or "Unknown Prospect"
    )


def _extract_org_id(entry: dict[str, Any]) -> str | None:
    entity = entry.get("entity") if isinstance(entry.get("entity"), dict) else {}
    value = (
        entry.get("organization_id")
        or entry.get("entity_id")
        or entity.get("id")
    )
    return str(value) if value else None


def _extract_status(field_values: list[dict[str, Any]]) -> tuple[str, str | None]:
    for field_value in field_values:
        name = str(
            field_value.get("field_name")
            or field_value.get("name")
            or (field_value.get("field") or {}).get("name")
            or ""
        ).lower()
        if "status" not in name and "stage" not in name:
            continue
        value = field_value.get("value")
        if isinstance(value, dict):
            status = value.get("text") or value.get("name") or value.get("value")
        else:
            status = value
        if status:
            return str(status), str(field_value.get("id")) if field_value.get("id") else None
    return "", None


def _normalize_owner_emails(owner_emails: list[str] | str | None) -> frozenset[str]:
    if not owner_emails:
        return frozenset()
    if isinstance(owner_emails, str):
        owner_emails = [owner_emails]
    return frozenset(
        email.strip().lower() for email in owner_emails if email and email.strip()
    )


def _extract_owner_email(field_values: list[dict[str, Any]]) -> str:
    for field_value in field_values:
        name = str(
            field_value.get("field_name")
            or field_value.get("name")
            or (field_value.get("field") or {}).get("name")
            or ""
        ).lower()
        if "owner" not in name:
            continue
        value = field_value.get("value")
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, dict):
                email = candidate.get("email") or candidate.get("email_address")
                if email:
                    return str(email)
            elif isinstance(candidate, str) and "@" in candidate:
                return candidate
    return ""


def _latest_note_date(notes: list[Note]) -> str:
    dated = sorted((note.created_at or "" for note in notes), reverse=True)
    if dated and dated[0]:
        return dated[0][:10]
    return ""


def _find_status_option_id(fields_payload: Any, status_name: str) -> str | None:
    fields = fields_payload.get("fields", fields_payload) if isinstance(fields_payload, dict) else fields_payload
    if not isinstance(fields, list):
        return None
    for field in fields:
        if not isinstance(field, dict):
            continue
        options = field.get("dropdown_options") or field.get("options") or []
        for option in options:
            if isinstance(option, dict) and option.get("text") == status_name:
                return str(option.get("id"))
            if isinstance(option, dict) and option.get("name") == status_name:
                return str(option.get("id"))
    return None


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--scan", action="store_true")
    args = parser.parse_args()
    if not args.scan:
        parser.error("only --scan is supported")

    from scripts.secrets import SecretConfig, SecretResolver
    from scripts.seren_client import GatewayClient

    config = _load_config(Path(args.config))
    secret_resolver = SecretResolver(
        GatewayClient.from_env(),
        SecretConfig.from_mapping(config.get("secrets", {})),
    )
    client = AffinityClient(secret_resolver.get_affinity_key())
    affinity_cfg = config.get("affinity", {})
    prospect_list = client.find_list(affinity_cfg["list_name"])
    print(json.dumps({"list": prospect_list.get("name"), "status": "scan wiring ready"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
