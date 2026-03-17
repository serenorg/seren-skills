from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"
SPEC = importlib.util.spec_from_file_location("key_recovery_agent", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_partial_seed_classification_is_diy_moderate() -> None:
    answers = MODULE.Answers(
        loss_type="seed",
        wallet_type="MetaMask",
        knowledge_summary="I have 11 of 12 words and 1 missing word. Order is known.",
        known_identifier="0x1234567890abcdef1234567890abcdef12345678",
        value_range="$1,000 - $10,000",
        attempted_steps="none",
        scam_check=MODULE.ScamCheck(shared_with_service=False),
    )

    diagnosis = MODULE.classify(answers)

    assert diagnosis.scenario_id == 2
    assert diagnosis.feasibility == "DIY-moderate"


def test_exchange_case_redirects() -> None:
    answers = MODULE.Answers(
        loss_type="exchange",
        wallet_type="exchange",
        knowledge_summary="I cannot log in to the exchange anymore.",
        known_identifier="",
        value_range="Prefer not to say",
        attempted_steps="password reset",
        scam_check=MODULE.ScamCheck(shared_with_service=False),
    )

    diagnosis = MODULE.classify(answers)

    assert diagnosis.scenario_id == 7
    assert diagnosis.recommended_path == "redirect"


def test_report_masks_identifier() -> None:
    answers = MODULE.Answers(
        loss_type="password",
        wallet_type="Electrum",
        knowledge_summary="I still have the wallet file and remember part of the password.",
        known_identifier="bc1qexample1234567890abcdefghijklmnop",
        value_range="Under $1,000",
        attempted_steps="btcrecover",
        scam_check=MODULE.ScamCheck(shared_with_service=True, upfront_fee=True),
    )
    diagnosis = MODULE.classify(answers)
    report = MODULE.build_report(answers, diagnosis, technical_user=True)

    assert report["known_identifier_available"] is True
    assert report["known_identifier_masked"].startswith("bc1qex")
    assert report["known_identifier_masked"].endswith("mnop")
    assert report["scam_exposure"]["exposed"] is True


def test_btcrecover_rejects_inline_secret_material() -> None:
    config = {
        "inputs": {
            "technical_mode": True,
            "allow_local_btcrecover": True,
            "user_confirmed_understands_risk": True,
        },
        "btcrecover": {
            "repo_path": ".",
            "python_bin": "python3",
            "request": {
                "mode": "partial-seed",
                "seed_phrase": "abandon abandon abandon",
            },
        },
    }

    try:
        MODULE.btcrecover_request(config)
    except MODULE.BtcrecoverSafetyError as exc:
        assert "Inline secret material" in str(exc)
    else:
        raise AssertionError("Expected inline secret material to be rejected")


def test_btcrecover_requires_consent_gates() -> None:
    diagnosis = MODULE.Diagnosis(
        scenario_id=1,
        scenario_name="Wallet password lost, file exists",
        feasibility="DIY-easy",
        explanation="x",
        next_steps=[],
        diy_commands=[],
        recommended_path="diy",
        honest_assessment="x",
    )
    config = {"inputs": {"technical_mode": True}}

    try:
        MODULE.ensure_btcrecover_allowed(config=config, diagnosis=diagnosis, technical_user=True)
    except MODULE.BtcrecoverSafetyError as exc:
        assert "allow_local_btcrecover" in str(exc)
    else:
        raise AssertionError("Expected missing gate to be rejected")


def test_build_wallet_password_btcrecover_command(tmp_path: Path) -> None:
    repo = tmp_path / "btcrecover"
    repo.mkdir()
    (repo / "btcrecover.py").write_text("print('ok')\n", encoding="utf-8")
    wallet = tmp_path / "wallet.dat"
    wallet.write_text("wallet", encoding="utf-8")
    tokenlist = tmp_path / "tokens.txt"
    tokenlist.write_text("token", encoding="utf-8")

    config = {
        "btcrecover": {
            "repo_path": str(repo),
            "python_bin": "python3",
            "request": {
                "mode": "wallet-password",
                "wallet_file": str(wallet),
                "tokenlist_file": str(tokenlist),
            },
        }
    }
    diagnosis = MODULE.Diagnosis(
        scenario_id=1,
        scenario_name="Wallet password lost, file exists",
        feasibility="DIY-easy",
        explanation="x",
        next_steps=[],
        diy_commands=[],
        recommended_path="diy",
        honest_assessment="x",
    )

    command, cwd = MODULE.build_btcrecover_command(config, diagnosis)

    assert cwd == repo
    assert command[0] == "python3"
    assert command[1].endswith("btcrecover.py")
    assert "--wallet" in command
    assert "--tokenlist" in command


def test_hashcat_rejects_inline_secret_material() -> None:
    config = {
        "inputs": {
            "technical_mode": True,
            "allow_local_hashcat": True,
            "user_confirmed_understands_risk": True,
        },
        "hashcat": {
            "request": {
                "mode": "wallet-password",
                "password": "hunter2",
            },
        },
    }

    try:
        MODULE.hashcat_request(config)
    except MODULE.HashcatSafetyError as exc:
        assert "Inline secret material" in str(exc)
    else:
        raise AssertionError("Expected inline secret material to be rejected")


def test_hashcat_requires_consent_gates() -> None:
    diagnosis = MODULE.Diagnosis(
        scenario_id=1,
        scenario_name="Wallet password lost, file exists",
        feasibility="DIY-easy",
        explanation="x",
        next_steps=[],
        diy_commands=[],
        recommended_path="diy",
        honest_assessment="x",
    )
    config = {"inputs": {"technical_mode": True}}

    try:
        MODULE.ensure_hashcat_allowed(config=config, diagnosis=diagnosis, technical_user=True)
    except MODULE.HashcatSafetyError as exc:
        assert "allow_local_hashcat" in str(exc)
    else:
        raise AssertionError("Expected missing gate to be rejected")


def test_build_hashcat_command_with_wordlist_and_rule(tmp_path: Path) -> None:
    hash_file = tmp_path / "wallet.hash"
    hash_file.write_text("dummy-hash\n", encoding="utf-8")
    wordlist = tmp_path / "candidates.txt"
    wordlist.write_text("candidate\n", encoding="utf-8")
    rule = tmp_path / "rule.rule"
    rule.write_text(":\n", encoding="utf-8")

    config = {
        "hashcat": {
            "binary_path": "hashcat",
            "request": {
                "mode": "wallet-password",
                "hash_mode": "11300",
                "hash_file": str(hash_file),
                "attack_mode": 0,
                "wordlist_file": str(wordlist),
                "rule_file": str(rule),
                "session": "test-session",
            },
        }
    }
    diagnosis = MODULE.Diagnosis(
        scenario_id=1,
        scenario_name="Wallet password lost, file exists",
        feasibility="DIY-easy",
        explanation="x",
        next_steps=[],
        diy_commands=[],
        recommended_path="diy",
        honest_assessment="x",
    )

    command, cwd = MODULE.build_hashcat_command(config, diagnosis)

    assert cwd == hash_file.parent
    assert command[:5] == ["hashcat", "-m", "11300", "-a", "0"]
    assert "--session" in command
    assert str(hash_file) in command
    assert str(wordlist) in command
    assert "-r" in command


def test_maybe_send_report_returns_manual_instructions() -> None:
    result = MODULE.maybe_send_report(
        report={
            "scenario_name": "Wallet password lost, file exists",
        },
        report_markdown="# report\n",
        config={
            "inputs": {
                "share_report": True,
            },
            "sponsor": {
                "intake_email": "hello@serendb.com",
            },
        },
        send_report=True,
    )

    assert result["status"] == "instructions"
    assert result["channel"] == "manual_email"
    assert result["to"] == "hello@serendb.com"
    assert "forward" in result["message"].lower() or "forward" in result["forwarding_note"].lower()


def test_maybe_send_report_requires_consent() -> None:
    result = MODULE.maybe_send_report(
        report={"scenario_name": "case"},
        report_markdown="# report\n",
        config={"inputs": {"share_report": False}},
        send_report=True,
    )

    assert result["status"] == "blocked"
    assert "share_report" in result["reason"]
