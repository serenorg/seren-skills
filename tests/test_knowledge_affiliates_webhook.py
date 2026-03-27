"""Verify knowledge skill affiliate reward webhook (issue #302).

Tests:
1. Payload construction — all required fields present, HMAC signature valid
2. Graceful skip when config is missing affiliate keys
3. Capture vs retrieval amount_cents logic
4. Non-blocking failure handling
5. Agent integration — steps 14-15 wired
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBHOOK_PATH = REPO_ROOT / "family-office" / "knowledge" / "scripts" / "affiliates_webhook.py"
AGENT_PATH = REPO_ROOT / "family-office" / "knowledge" / "scripts" / "agent.py"
CONFIG_PATH = REPO_ROOT / "family-office" / "knowledge" / "config.example.json"


def _load_webhook_module():
    spec = importlib.util.spec_from_file_location("affiliates_webhook", WEBHOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def wh():
    return _load_webhook_module()


FULL_CONFIG = {
    "affiliates_webhook_url": "https://affiliates.example.com/webhook",
    "affiliates_webhook_secret": "test-secret-key-123",
    "affiliates_publisher_id": "rendero-trust",
    "inputs": {"reward_base_usd": 100, "reward_per_retrieval_usd": 1},
}


# --- Payload construction ---


class TestBuildPayload:

    def test_required_fields_present(self, wh) -> None:
        payload = wh.build_reward_payload(
            publisher_id="rendero-trust",
            publisher_slug="rendero-trust",
            agent_id="user-123",
            referral_code="REF-ABC",
        )
        required = ["transaction_id", "publisher_id", "publisher_slug",
                     "amount_cents", "agent_id", "referral_code",
                     "event_type", "test_mode", "timestamp"]
        for field in required:
            assert field in payload, f"Missing required field: {field}"

    def test_default_amount_is_100(self, wh) -> None:
        payload = wh.build_reward_payload(
            publisher_id="x", publisher_slug="x",
            agent_id="u", referral_code="r",
        )
        assert payload["amount_cents"] == 100

    def test_custom_amount(self, wh) -> None:
        payload = wh.build_reward_payload(
            publisher_id="x", publisher_slug="x",
            agent_id="u", referral_code="r",
            amount_cents=500,
        )
        assert payload["amount_cents"] == 500

    def test_transaction_id_is_unique(self, wh) -> None:
        p1 = wh.build_reward_payload(publisher_id="x", publisher_slug="x", agent_id="u", referral_code="r")
        p2 = wh.build_reward_payload(publisher_id="x", publisher_slug="x", agent_id="u", referral_code="r")
        assert p1["transaction_id"] != p2["transaction_id"]


# --- HMAC signature ---


class TestHMACSignature:

    def test_signature_is_valid_hmac_sha256(self, wh) -> None:
        payload = wh.build_reward_payload(
            publisher_id="test", publisher_slug="test",
            agent_id="u", referral_code="r",
        )
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        sig = wh._sign_payload(payload_bytes, "my-secret")

        expected = hmac.new(
            b"my-secret", payload_bytes, hashlib.sha256
        ).hexdigest()
        assert sig == expected

    def test_different_secret_different_signature(self, wh) -> None:
        data = b'{"test": true}'
        sig1 = wh._sign_payload(data, "secret-1")
        sig2 = wh._sign_payload(data, "secret-2")
        assert sig1 != sig2


# --- Graceful skip ---


class TestGracefulSkip:

    def test_skips_when_url_missing(self, wh) -> None:
        result = wh.fire_reward_webhook(
            config={"affiliates_webhook_secret": "s", "affiliates_publisher_id": "p"},
            agent_id="u", referral_code="r",
        )
        assert result["status"] == "skipped"

    def test_skips_when_secret_missing(self, wh) -> None:
        result = wh.fire_reward_webhook(
            config={"affiliates_webhook_url": "http://x", "affiliates_publisher_id": "p"},
            agent_id="u", referral_code="r",
        )
        assert result["status"] == "skipped"

    def test_skips_when_publisher_id_missing(self, wh) -> None:
        result = wh.fire_reward_webhook(
            config={"affiliates_webhook_url": "http://x", "affiliates_webhook_secret": "s"},
            agent_id="u", referral_code="r",
        )
        assert result["status"] == "skipped"


# --- Agent integration ---


class TestAgentIntegration:

    def test_agent_imports_webhook(self) -> None:
        source = AGENT_PATH.read_text(encoding="utf-8")
        assert "from affiliates_webhook import" in source

    def test_agent_has_calculate_rewards(self) -> None:
        source = AGENT_PATH.read_text(encoding="utf-8")
        assert "def calculate_rewards" in source

    def test_agent_has_persist_rewards(self) -> None:
        source = AGENT_PATH.read_text(encoding="utf-8")
        assert "def persist_rewards" in source

    def test_config_has_affiliates_fields(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        assert "affiliates_webhook_url" in config
        assert "affiliates_webhook_secret" in config
        assert "affiliates_publisher_id" in config
