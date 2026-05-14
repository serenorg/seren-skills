"""Unit tests for scripts/agent.py.

Cover the pure CLI surface — argument parsing, config-load errors,
and the live/dry-run gates — without touching Playwright or 1Password.

The end-to-end dry-run (op → SSO → first-lead) is exercised at the
Phase 1 operator checkpoint, not here. That run is the contract; this
suite just guards against the CLI being reshaped in a way that breaks
the contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import agent


# --------------------------------------------------------------------- #
# Config loading                                                        #
# --------------------------------------------------------------------- #


def test_load_config_raises_when_file_missing(tmp_path: Path):
    missing = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError, match="config.json"):
        agent._load_config(missing)


def test_load_config_returns_parsed_json(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"inputs": {"salesforce_org_url": "https://x.com"}}))
    assert agent._load_config(cfg) == {
        "inputs": {"salesforce_org_url": "https://x.com"}
    }


def test_resolve_salesforce_org_url_returns_value():
    cfg = {"inputs": {"salesforce_org_url": "https://acme.lightning.force.com"}}
    assert (
        agent._resolve_salesforce_org_url(cfg)
        == "https://acme.lightning.force.com"
    )


def test_resolve_salesforce_org_url_rejects_empty_string():
    cfg = {"inputs": {"salesforce_org_url": ""}}
    with pytest.raises(ValueError, match="empty"):
        agent._resolve_salesforce_org_url(cfg)


def test_resolve_salesforce_org_url_rejects_example_placeholder():
    """The committed `config.example.json` ships with a literal
    `<org-subdomain>` placeholder. We must reject it rather than
    silently navigating to nonsense."""

    cfg = {"inputs": {"salesforce_org_url": "https://<org-subdomain>.lightning.force.com"}}
    with pytest.raises(ValueError, match="placeholder"):
        agent._resolve_salesforce_org_url(cfg)


def test_resolve_salesforce_org_url_rejects_missing_inputs_key():
    with pytest.raises(ValueError):
        agent._resolve_salesforce_org_url({})


# --------------------------------------------------------------------- #
# CLI gates                                                             #
# --------------------------------------------------------------------- #


def test_main_rejects_run_without_dry_run(capsys, tmp_path: Path):
    """Phase 1 has no write paths; the `--dry-run` flag is required.

    Without it, the CLI must exit non-zero and not invoke any
    downstream module.
    """

    rc = agent.main(["--command", "run", "--config", str(tmp_path / "x.json")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "--dry-run only" in captured.err


def test_main_rejects_allow_live_in_phase_1(capsys, tmp_path: Path):
    """`--allow-live` is reserved for Phase 2+. Phase 1 refuses it."""

    rc = agent.main(
        [
            "--command",
            "run",
            "--dry-run",
            "--allow-live",
            "--config",
            str(tmp_path / "x.json"),
        ]
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "reserved for Phase 2" in captured.err


# --------------------------------------------------------------------- #
# Dry-run orchestration (downstream modules faked)                       #
# --------------------------------------------------------------------- #


def test_main_dry_run_prints_first_lead(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
):
    """When dry-run executes, the first-lead row must end up on stdout.

    Both downstream calls — op credential read and Playwright/SSO/
    Lead orchestration — are monkeypatched. The test guards the
    contract: parsed args → run dry-run → print structured row.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {"inputs": {"salesforce_org_url": "https://acme.lightning.force.com"}}
        )
    )

    from scripts.sf import client as sf_client

    fake_lead = sf_client.LeadRow(
        record_id="00Q5g00000XYZAbc",
        name="Acme GmbH",
        source_url="https://acme.lightning.force.com/lightning/o/Lead/list",
    )

    captured_kwargs: dict = {}

    def fake_run_dry_run(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_lead

    monkeypatch.setattr(agent, "_run_dry_run", fake_run_dry_run)

    rc = agent.main(
        ["--command", "run", "--dry-run", "--config", str(cfg)]
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert "00Q5g00000XYZAbc" in captured.out
    assert "Acme GmbH" in captured.out

    # Org URL flowed through from config to the runner.
    assert (
        captured_kwargs["salesforce_org_url"]
        == "https://acme.lightning.force.com"
    )


# --------------------------------------------------------------------- #
# Direct-script invocation (issue #541)                                  #
# --------------------------------------------------------------------- #


def test_direct_script_invocation_resolves_sibling_imports():
    """`python scripts/agent.py --help` must succeed.

    Issue #541: launching the CLI as a script — the form documented
    in SKILL.md — used to die with `ModuleNotFoundError: No module
    named 'scripts'` because Python puts `scripts/` on `sys.path`,
    not the skill root. The fix is a one-line `sys.path` nudge at
    the top of `scripts/agent.py`. This test is the canary that
    locks the fix in: it runs the actual script in a fresh
    subprocess (no pytest path discovery, no PYTHONPATH inheritance)
    and asserts the import chain resolves cleanly.

    A pure-import test would be insufficient — pytest puts the skill
    root on `sys.path` itself, so the bug only surfaces in a real
    subprocess launched the way the SKILL.md documents.
    """

    skill_root = Path(__file__).resolve().parent.parent
    agent_script = skill_root / "scripts" / "agent.py"
    assert agent_script.exists(), agent_script

    # Strip PYTHONPATH so an inherited skill-root entry can't hide the
    # bug. Run from a directory that is NOT the skill root for the
    # same reason — sys.path[0] gets set to the script's parent
    # (`scripts/`), exactly as in the SKILL.md-documented invocation.
    env = {"PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(agent_script), "--help"],
        cwd="/tmp",
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"direct-script invocation failed: rc={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # Sanity check that argparse really rendered (i.e. we hit `main`,
    # not a partial import). The CLI's `prog` name is stable.
    assert "pk-lead-intelligence" in result.stdout
