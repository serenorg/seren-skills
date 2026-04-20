"""Critical smoke test for the Real Estate Manager DD Memo skill.

Uses importlib to load the skill's agent module by path. Avoids the
sys.modules collision that would otherwise happen when the whole
family-office/ tree is collected in one pytest run (every leaf has
scripts/agent.py).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
_AGENT_PATH = HERE.parent / "scripts" / "agent.py"


def _load_agent():
    # Unique module name per skill avoids sys.modules collisions across the
    # 55-leaf test collection.
    mod_name = f"family_office_{HERE.parent.name.replace('-', '_')}_agent"
    spec = importlib.util.spec_from_file_location(mod_name, _AGENT_PATH)
    assert spec and spec.loader, f"cannot load agent at {_AGENT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(agent):
    return {key: "fixture-value" for key, _ in agent.INTERVIEW_QUESTIONS}


def test_agent_runs_end_to_end_and_writes_artifact(tmp_path: Path) -> None:
    agent = _load_agent()
    answers = agent.run_interview(fixture=_fixture(agent), tty=False)
    assert set(answers) == {key for key, _ in agent.INTERVIEW_QUESTIONS}
    manifest = agent.write_artifact(answers, base=tmp_path)

    assert manifest["skill"] == agent.SKILL_NAME
    assert manifest["pillar"] == agent.PILLAR
    assert manifest["artifact_name"] == agent.ARTIFACT_NAME
    assert manifest["artifact_version"] == 1
    assert len(manifest["content_hash"]) == 64

    out_dir = Path(manifest["out_dir"])
    assert (out_dir / "artifact.md").exists()
    assert (out_dir / "interview.json").exists()
    assert (out_dir / "manifest.json").exists()

    recaptured = json.loads((out_dir / "interview.json").read_text("utf-8"))
    assert recaptured == answers


def test_interview_rejects_missing_fixture_key() -> None:
    agent = _load_agent()
    partial = _fixture(agent)
    partial.pop(next(iter(partial)))
    with pytest.raises(ValueError):
        agent.run_interview(fixture=partial, tty=False)
