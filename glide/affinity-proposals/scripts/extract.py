from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


# Routable seren-models id. `"default"` is not a model — the publisher
# routes by explicit provider-qualified id. GPT-5.5 is the product default
# for proposal extraction (issue #875); verified live-routable on seren-models.
DEFAULT_MODEL = "openai/gpt-5.5"


@dataclass
class ExtractionConfig:
    model: str = DEFAULT_MODEL
    default_structure: str = "offshore"


@dataclass
class ProposalProfile:
    client_name: str
    description: str
    seeking: list[str] = field(default_factory=lambda: ["feeder funds"])
    structure: str = "offshore"
    fund_name: str = ""
    advisor_name: str = ""
    confidence: str = "medium"


class ModelClient(Protocol):
    def chat_json(self, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> dict[str, Any]:
        ...


OFFSHORE_CUES = (
    "bvi",
    "offshore",
    "non-us",
    "non us",
    "non-u.s.",
    "blocker",
    "qp",
    "qualified purchaser",
)
ONSHORE_PATTERNS = (
    r"\bonshore\b",
    r"\bdelaware\b",
    r"\bu\.s\.\b",
    r"\bus investors\b",
    r"\bdomestic\b",
)


def classify_structure(note_text: str, *, default: str = "offshore") -> str:
    lowered = note_text.lower()
    if any(cue in lowered for cue in OFFSHORE_CUES):
        return "offshore"
    if any(re.search(pattern, lowered) for pattern in ONSHORE_PATTERNS):
        return "onshore"
    return default


PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "client_name",
        "description",
        "seeking",
        "structure",
        "fund_name",
        "advisor_name",
    ],
    "properties": {
        "client_name": {"type": "string"},
        "description": {"type": "string"},
        "seeking": {"type": "array", "items": {"type": "string"}},
        "structure": {"type": "string", "enum": ["offshore", "onshore"]},
        "fund_name": {"type": "string"},
        "advisor_name": {"type": "string"},
        "confidence": {"type": "string"},
    },
}


def extract_profile(
    note_text: str,
    *,
    org_name: str,
    model_client: ModelClient,
    config: ExtractionConfig,
) -> ProposalProfile:
    messages = [
        {
            "role": "system",
            "content": (
                "Extract a proposal profile from a CRM meeting note. "
                "Use only the note and organization name. Return JSON."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"organization": org_name, "meeting_note": note_text},
                sort_keys=True,
            ),
        },
    ]
    response = model_client.chat_json(messages, PROFILE_SCHEMA)
    structure = str(response.get("structure") or "").lower()
    if structure not in {"offshore", "onshore"}:
        structure = classify_structure(note_text, default=config.default_structure)
    seeking = response.get("seeking") or ["feeder funds"]
    if isinstance(seeking, str):
        seeking = [seeking]
    return ProposalProfile(
        client_name=str(response.get("client_name") or org_name),
        description=str(response.get("description") or ""),
        seeking=[str(item) for item in seeking],
        structure=structure,
        fund_name=str(response.get("fund_name") or f"{org_name} Fund"),
        advisor_name=str(response.get("advisor_name") or org_name),
        confidence=str(response.get("confidence") or "medium"),
    )


class GatewayModelClient:
    def __init__(self, gateway: Any, *, model: str = DEFAULT_MODEL) -> None:
        self.gateway = gateway
        self.model = model

    def chat_json(self, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> dict[str, Any]:
        return self.gateway.chat_json(
            messages=messages,
            response_schema=response_schema,
            model=self.model,
            temperature=0,
        )
