from __future__ import annotations

from scripts.extract import (
    DEFAULT_MODEL,
    ExtractionConfig,
    GatewayModelClient,
    classify_structure,
    extract_profile,
)


class FakeModelClient:
    def __init__(self) -> None:
        self.messages: list[list[dict]] = []

    def chat_json(self, messages, response_schema):
        self.messages.append(messages)
        return {
            "client_name": "Acme Capital",
            "description": "Acme is preparing an institutional feeder launch.",
            "seeking": ["feeder funds", "launch planning"],
            "structure": "offshore",
            "fund_name": "Acme Credit Fund",
            "advisor_name": "Acme Advisors",
            "confidence": "high",
        }


def test_structure_classifier_defaults_ambiguous_to_offshore():
    assert classify_structure("US taxable investors and Delaware fund") == "onshore"
    assert classify_structure("non-US investors need a BVI feeder blocker") == "offshore"
    assert classify_structure("Discussed launch timing and operating model") == "offshore"


def test_extract_profile_calls_model_with_note_and_parses_response():
    client = FakeModelClient()

    profile = extract_profile(
        "Meeting covered BVI feeder and non-US investors.",
        org_name="Acme Capital",
        model_client=client,
        config=ExtractionConfig(),
    )

    assert profile.client_name == "Acme Capital"
    assert profile.structure == "offshore"
    assert "Acme Capital" in str(client.messages[0])
    assert "BVI feeder" in str(client.messages[0])


class RecordingGateway:
    def __init__(self) -> None:
        self.model: str | None = None

    def chat_json(self, *, messages, response_schema, model, temperature):
        self.model = model
        return {
            "client_name": "X",
            "description": "",
            "seeking": [],
            "structure": "offshore",
            "fund_name": "",
            "advisor_name": "",
        }


def test_gateway_model_client_sends_real_model_id_by_default():
    gateway = RecordingGateway()
    GatewayModelClient(gateway).chat_json([{"role": "user", "content": "hi"}], {})
    assert gateway.model == DEFAULT_MODEL
    assert gateway.model != "default"
    assert "/" in gateway.model  # provider-qualified, routable id


def test_gateway_model_client_model_is_overridable():
    gateway = RecordingGateway()
    GatewayModelClient(gateway, model="anthropic/claude-opus-4-8").chat_json([], {})
    assert gateway.model == "anthropic/claude-opus-4-8"
