from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"
CONFIG_EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"


def test_skill_md_contains_anti_repetition_guardrails() -> None:
    content = SKILL_MD.read_text(encoding="utf-8")
    assert "Prefer consolidation over expansion." in content
    assert "Stop expanding once additions are refinements rather than net-new behavior." in content
    assert "Observed fact" in content
    assert "Inference" in content
    assert "Recommendation" in content
    assert "Open question" in content


def test_agent_outputs_guardrail_summary_from_example_config() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--config", str(CONFIG_EXAMPLE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["skill"] == "strategic-account-manager"
    assert payload["mode"] == "prep"
    assert "Prefer consolidation over expansion." in payload["guardrails"]
    assert payload["canonical_sections"][0] == "Core Product Shape"
