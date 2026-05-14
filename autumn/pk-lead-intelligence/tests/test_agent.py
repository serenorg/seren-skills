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
