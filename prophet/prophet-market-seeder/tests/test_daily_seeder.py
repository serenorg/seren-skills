from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "daily_seeder.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("daily_seeder", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_otp_from_snippet() -> None:
    ds = _load_module()
    msg = {"snippet": "Your verification code is 482937. It expires in 10 minutes.", "payload": {}}
    body = ds._extract_email_body(msg)
    match = ds.OTP_CODE_PATTERN.search(body)
    assert match is not None
    assert match.group(1) == "482937"


def test_extract_otp_from_base64_body() -> None:
    ds = _load_module()
    raw = "Your login code is 193847 for Prophet."
    encoded = base64.urlsafe_b64encode(raw.encode()).decode()
    msg = {"snippet": "", "payload": {"body": {"data": encoded}}}
    body = ds._extract_email_body(msg)
    match = ds.OTP_CODE_PATTERN.search(body)
    assert match is not None
    assert match.group(1) == "193847"


def test_extract_otp_from_multipart() -> None:
    ds = _load_module()
    raw = "Code: 556677"
    encoded = base64.urlsafe_b64encode(raw.encode()).decode()
    msg = {
        "snippet": "",
        "payload": {
            "body": {},
            "parts": [
                {"mimeType": "text/html", "body": {"data": ""}},
                {"mimeType": "text/plain", "body": {"data": encoded}},
            ],
        },
    }
    body = ds._extract_email_body(msg)
    match = ds.OTP_CODE_PATTERN.search(body)
    assert match is not None
    assert match.group(1) == "556677"


def test_score_polymarket_candidate_contestedness() -> None:
    ds = _load_module()
    # 50/50 market with high volume should score highest
    contested = ds.score_polymarket_candidate({"probability": 0.5, "volume_usd": 1_000_000})
    # 90% market with same volume should score lower
    lopsided = ds.score_polymarket_candidate({"probability": 0.9, "volume_usd": 1_000_000})
    assert contested > lopsided


def test_polymarket_to_prophet_dedup_and_limit() -> None:
    ds = _load_module()
    rows = [
        {"title": "Will X happen?", "probability": 0.5, "volume_usd": 500000},
        {"title": "Will Y happen?", "probability": 0.45, "volume_usd": 400000},
        {"title": "Already submitted", "probability": 0.5, "volume_usd": 900000},
        {"title": "Will Z happen?", "probability": 0.6, "volume_usd": 300000},
    ]
    questions = ds.polymarket_to_prophet_questions(
        rows, submit_limit=2, recent_titles=["already submitted"]
    )
    assert len(questions) == 2
    assert "Already submitted" not in questions
    assert "Will X happen?" in questions


def test_run_daily_seed_missing_api_key(monkeypatch) -> None:
    ds = _load_module()
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    result = ds.run_daily_seed("/nonexistent/config.json")
    assert result["status"] == "error"
    assert "SEREN_API_KEY" in result["error"]
