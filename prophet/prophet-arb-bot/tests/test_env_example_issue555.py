"""Issue #555: .env.example must list every env var the runtime enforces."""

from __future__ import annotations

from pathlib import Path

from agent import POLYMARKET_REQUIRED_ENV_VARS


def test_env_example_lists_polymarket_required_env_vars() -> None:
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    missing = [
        name for name in POLYMARKET_REQUIRED_ENV_VARS if f"{name}=" not in text
    ]
    assert not missing, (
        f".env.example is missing keys enforced by POLYMARKET_REQUIRED_ENV_VARS: {missing}"
    )
