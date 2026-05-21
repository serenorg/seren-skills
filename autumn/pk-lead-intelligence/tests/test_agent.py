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
    """`--command run` with neither `--dry-run` nor `--allow-live`
    is refused — Phase 4 unlocked `--allow-live` as an alternative
    to `--dry-run`, but bare `run` still cannot proceed (we never
    enrich silently without an operator opt-in)."""

    rc = agent.main(["--command", "run", "--config", str(tmp_path / "x.json")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "requires --dry-run or --allow-live" in captured.err


def test_main_run_allow_live_requires_live_mode_config(
    capsys, tmp_path: Path
):
    """Phase 4: `--allow-live` for `--command run` requires the
    `inputs.live_mode=true` config gate. Defense in depth —
    either gate alone refuses to write to a live Lead record.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "inputs": {
                    "salesforce_org_url": "https://acme.lightning.force.com",
                    "live_mode": False,
                }
            }
        )
    )
    rc = agent.main(
        [
            "--command",
            "run",
            "--allow-live",
            "--config",
            str(cfg),
        ]
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "live_mode" in captured.err


def test_main_run_emits_single_line_summary_for_cron(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    """The cron parses one line of stdout per run.

    The contract is a single line prefixed with
    `pk-lead-intelligence run:` carrying the run-state key/value
    pairs the seren-cron `execution_results` table records. The
    test asserts the prefix and the presence of the run-status
    keys so the cron has a stable parse target.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {"inputs": {"salesforce_org_url": "https://acme.lightning.force.com"}}
        )
    )

    from scripts.research.claude_hypothesis import Hypothesis
    from scripts.research.linkedin_search import LinkedInCandidate
    from scripts.research.perplexity import PerplexityResearch
    from scripts.sf import client as sf_client
    from scripts.sf import enrich_lead
    from scripts.output.note_renderer import NoteSection, RenderedNote

    fake_lead = sf_client.LeadRow(
        record_id="00Q5g00000XYZAbc",
        name="Acme GmbH",
        source_url="https://acme.lightning.force.com/lightning/o/Lead/list",
    )
    fake_note = RenderedNote(
        title="t", sections=[NoteSection("Lead", "x")],
        enriched_at_utc="2026-05-14T10:30:00Z",
    )
    fake_enrichment = enrich_lead.EnrichmentResult(
        note=fake_note,
        docx_path=tmp_path / "note.docx",
        perplexity=PerplexityResearch(summary="s", citations=[], raw_text=""),
        linkedin=None,
        hypothesis=Hypothesis(text="h", recommended_action="a"),
    )

    monkeypatch.setattr(agent, "_run_dry_run", lambda **k: fake_lead)
    monkeypatch.setattr(agent, "_run_enrichment", lambda **k: fake_enrichment)

    rc = agent.main(
        ["--command", "run", "--dry-run", "--config", str(cfg)]
    )

    assert rc == 0
    out = capsys.readouterr().out
    summary_lines = [
        ln for ln in out.splitlines()
        if ln.startswith("pk-lead-intelligence run:")
    ]
    assert len(summary_lines) == 1, (
        f"Expected exactly one summary line. Got: {summary_lines!r}"
    )
    summary = summary_lines[0]
    # The cron records these keys verbatim; their presence is a contract.
    assert "command=run" in summary
    assert "dry_run=true" in summary
    assert "notes_written=" in summary


# --------------------------------------------------------------------- #
# Phase 3 provisioning CLI gates                                         #
# --------------------------------------------------------------------- #


def test_main_provision_requires_live_mode_in_config(
    capsys, tmp_path: Path
):
    """`--allow-live` alone is not enough — `inputs.live_mode` must
    also be true. Defense in depth: a stray CLI flag cannot drive
    Salesforce writes if the operator has not flipped the config gate.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "inputs": {
                    "salesforce_org_url": "https://acme.lightning.force.com",
                    "live_mode": False,
                }
            }
        )
    )
    rc = agent.main(
        [
            "--command",
            "provision",
            "--allow-live",
            "--config",
            str(cfg),
        ]
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "live_mode" in captured.err


def test_main_provision_dry_run_prints_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    """Dry-run provision prints the plan without driving the UI.

    The seam `_run_provision` is monkeypatched — tests do not need
    Playwright or a live Salesforce. The contract: parsed args →
    dispatch to provision → print the three artifact summaries.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "inputs": {
                    "salesforce_org_url": "https://acme.lightning.force.com",
                    "live_mode": False,
                }
            }
        )
    )

    from scripts.sf import build_all_sources_leads_report as all_leads_report
    from scripts.sf import build_pk_lead_dashboard as lead_dashboard
    from scripts.sf import build_pk_opp_artifacts as opp_artifacts

    fake_summary = agent.ProvisionSummary(
        all_sources_report=all_leads_report.ReportResult(
            spec=all_leads_report.ALL_SOURCES_PK_LEADS_REPORT_SPEC,
            status="dry_run",
            url=all_leads_report.PINNED_REPORT_URL,
        ),
        lead_dashboard=lead_dashboard.DashboardResult(
            spec=lead_dashboard.PK_LEAD_DASHBOARD_SPEC,
            status="dry_run",
            url=lead_dashboard.PINNED_DASHBOARD_URL,
        ),
        opp_dashboard=opp_artifacts.DashboardResult(
            spec=opp_artifacts.PK_OPP_PIPELINE_DASHBOARD_SPEC,
            status="dry_run",
            url=opp_artifacts.PINNED_DASHBOARD_URL,
        ),
    )

    captured_kwargs: dict = {}

    def fake_run_provision(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_summary

    monkeypatch.setattr(agent, "_run_provision", fake_run_provision)

    rc = agent.main(
        ["--command", "provision", "--dry-run", "--config", str(cfg)]
    )

    assert rc == 0
    assert captured_kwargs["dry_run"] is True
    out = capsys.readouterr().out
    # The three operator-owned artifacts are each surfaced so the
    # operator can eyeball the plan.
    assert "All Sources PK Leads" in out
    assert "PK Inbound Web Lead and Activity Tracking" in out
    assert "PK Inbound Web Lead and Opportunity Tracking" in out


# --------------------------------------------------------------------- #
# Dry-run orchestration (downstream modules faked)                       #
# --------------------------------------------------------------------- #


def test_main_dry_run_prints_first_lead_and_enrichment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
):
    """Phase 2 dry-run: lead row + enrichment .docx path land on stdout.

    Both downstream seams are monkeypatched — `_run_dry_run` (Playwright /
    1Password) and `_run_enrichment` (Perplexity / LinkedIn / Claude /
    python-docx). The test guards the contract: parsed args → fetch lead
    → enrich → print structured row + enrichment summary.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {"inputs": {"salesforce_org_url": "https://acme.lightning.force.com"}}
        )
    )

    from scripts.research.claude_hypothesis import Hypothesis
    from scripts.research.linkedin_search import LinkedInCandidate
    from scripts.research.perplexity import PerplexityResearch
    from scripts.sf import client as sf_client
    from scripts.sf import enrich_lead
    from scripts.output.note_renderer import NoteSection, RenderedNote

    fake_lead = sf_client.LeadRow(
        record_id="00Q5g00000XYZAbc",
        name="Acme GmbH",
        source_url="https://acme.lightning.force.com/lightning/o/Lead/list",
    )

    captured_kwargs: dict = {}

    def fake_run_dry_run(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_lead

    fake_note = RenderedNote(
        title="PK Lead Enrichment — Acme GmbH",
        sections=[NoteSection("Lead", "x")],
        enriched_at_utc="2026-05-14T10:30:00Z",
    )
    fake_docx_path = tmp_path / "output" / "00Q5g00000XYZAbc_Acme_GmbH.docx"
    fake_enrichment = enrich_lead.EnrichmentResult(
        note=fake_note,
        docx_path=fake_docx_path,
        perplexity=PerplexityResearch(summary="s", citations=[], raw_text=""),
        linkedin=LinkedInCandidate(
            url="https://www.linkedin.com/in/x/",
            title=None,
            match_confidence=42,
            reasons=["linkedin-profile-url"],
        ),
        hypothesis=Hypothesis(text="h", recommended_action="a"),
    )

    enrichment_kwargs: dict = {}

    def fake_run_enrichment(**kwargs):
        enrichment_kwargs.update(kwargs)
        return fake_enrichment

    monkeypatch.setattr(agent, "_run_dry_run", fake_run_dry_run)
    monkeypatch.setattr(agent, "_run_enrichment", fake_run_enrichment)

    rc = agent.main(
        ["--command", "run", "--dry-run", "--config", str(cfg)]
    )

    assert rc == 0
    captured = capsys.readouterr()
    # Phase 1 contract still holds.
    assert "00Q5g00000XYZAbc" in captured.out
    assert "Acme GmbH" in captured.out
    # Phase 2 additions are surfaced.
    assert str(fake_docx_path) in captured.out
    assert "42%" in captured.out

    # Org URL flowed through from config to the SSO runner.
    assert (
        captured_kwargs["salesforce_org_url"]
        == "https://acme.lightning.force.com"
    )
    # Enrichment received the fetched lead.
    assert enrichment_kwargs["lead"] is fake_lead


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
