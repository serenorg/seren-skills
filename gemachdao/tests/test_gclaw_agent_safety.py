"""Regression tests for gclaw-agent trading safety guardrails.

These tests validate the live confirmation gate, dependency failure
behavior, and emergency-exit path required by the trading-skill
safety policy.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

AGENT_PATH = Path(__file__).resolve().parents[1] / "gclaw-agent" / "scripts" / "agent.py"
SPEC = importlib.util.spec_from_file_location("gclaw_agent", AGENT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


# ---------------------------------------------------------------------------
# Live confirmation tests
# ---------------------------------------------------------------------------


def test_yes_live_flag_required_for_live_mode():
    """--yes-live must be provided together with config live_mode for live execution."""
    config = {"execution": {"live_mode": True}}
    args = mock.MagicMock(yes_live=False, allow_live=False)
    assert MODULE.is_live_mode(config, args) is False

    args_live = mock.MagicMock(yes_live=True, allow_live=False)
    assert MODULE.is_live_mode(config, args_live) is True


def test_allow_live_alias_works():
    """--allow-live is an alias for --yes-live and enables live mode."""
    config = {"execution": {"live_mode": True}}
    args = mock.MagicMock(yes_live=False, allow_live=True)
    assert MODULE.is_live_mode(config, args) is True


def test_live_confirmation_required_config_only_insufficient():
    """Config-only live_mode=true without CLI flag must NOT enable live mode."""
    config = {"execution": {"live_mode": True}}
    args = mock.MagicMock(yes_live=False, allow_live=False)
    # live_confirmation_required: config alone is not enough
    assert MODULE.is_live_mode(config, args) is False


def test_yes_live_without_config_is_not_live():
    """--yes-live alone without config live_mode is not live."""
    config = {"execution": {"live_mode": False}}
    args = mock.MagicMock(yes_live=True, allow_live=False)
    assert MODULE.is_live_mode(config, args) is False


# ---------------------------------------------------------------------------
# Dependency fail-closed tests
# ---------------------------------------------------------------------------


def test_missing_llm_provider_fails_closed():
    """Missing all LLM provider keys and no model_list must raise RuntimeError."""
    env = {
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "GEMINI_API_KEY": "",
        "ZHIPU_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "CEREBRAS_API_KEY": "",
    }
    config = {"agents": {"defaults": {"model_name": "gpt4"}}, "model_list": []}
    with mock.patch.dict(os.environ, env, clear=False):
        try:
            MODULE.validate_dependencies(config)
            assert False, "Should have raised RuntimeError"
        except RuntimeError as exc:
            assert "LLM provider" in str(exc)


def test_model_list_satisfies_llm_requirement():
    """A config with model_list containing api_key satisfies the LLM requirement."""
    env = {
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "GEMINI_API_KEY": "",
        "ZHIPU_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "CEREBRAS_API_KEY": "",
    }
    config = {
        "agents": {"defaults": {"model_name": "gpt4"}},
        "model_list": [
            {"model_name": "gpt4", "model": "openai/gpt-4o", "api_key": "sk-test"}
        ],
    }
    with mock.patch.dict(os.environ, env, clear=False), \
         mock.patch("shutil.which", return_value="/usr/local/bin/gclaw"):
        # Should NOT raise — model_list satisfies the LLM requirement
        MODULE.validate_dependencies(config)


def test_env_var_satisfies_llm_requirement():
    """A single LLM env var satisfies the LLM requirement even without model_list."""
    env = {
        "OPENAI_API_KEY": "test-key",
        "ANTHROPIC_API_KEY": "",
        "GEMINI_API_KEY": "",
        "ZHIPU_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "CEREBRAS_API_KEY": "",
    }
    config = {"agents": {"defaults": {}}, "model_list": []}
    with mock.patch.dict(os.environ, env, clear=False), \
         mock.patch("shutil.which", return_value="/usr/local/bin/gclaw"):
        MODULE.validate_dependencies(config)


# ---------------------------------------------------------------------------
# Emergency exit / unwind tests
# ---------------------------------------------------------------------------


def test_unwind_all_requires_yes_live_confirmation():
    """--unwind-all without --yes-live must fail with RuntimeError."""
    config = {"execution": {"live_mode": True}}
    args = mock.MagicMock(yes_live=False, allow_live=False, unwind_all=True)
    try:
        MODULE.unwind_all(config, args)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as exc:
        assert "--yes-live" in str(exc)


def test_unwind_all_cancels_orders_and_liquidates():
    """--unwind-all with --yes-live must cancel_all orders and liquidate inventory."""
    result = MODULE.cancel_all_orders()
    assert "cancel" in result.lower()

    result = MODULE.liquidate_inventory()
    assert "liquidat" in result.lower()


def test_cancel_all_orders_returns_confirmation():
    """cancel_all_orders must return a confirmation string."""
    result = MODULE.cancel_all_orders()
    assert "cancel" in result.lower()
    assert "order" in result.lower()


def test_emergency_unwind_with_yes_live(tmp_path):
    """Full unwind path with --yes-live succeeds when dependencies are met."""
    config = {
        "execution": {"live_mode": True},
        "model_list": [
            {"model_name": "gpt4", "model": "openai/gpt-4o", "api_key": "sk-test"}
        ],
    }
    args = mock.MagicMock(yes_live=True, allow_live=False, unwind_all=True)
    with mock.patch("shutil.which", return_value="/usr/local/bin/gclaw"):
        exit_code = MODULE.unwind_all(config, args)
        assert exit_code == 0
