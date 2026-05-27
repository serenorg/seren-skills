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

    from scripts.research.claude_angles import UltrasonicAngles
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
        title="t", sections=[NoteSection("CONTACT", "x")],
        enriched_at_utc="2026-05-14T10:30:00Z",
    )
    fake_enrichment = enrich_lead.EnrichmentResult(
        note=fake_note,
        docx_path=tmp_path / "note.docx",
        perplexity=PerplexityResearch(summary="s", citations=[], raw_text=""),
        linkedin=None,
        angles=UltrasonicAngles(angles=["a"]),
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

    from scripts.research.claude_angles import UltrasonicAngles
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
        title="PK Inbound Research — Acme GmbH / (company unknown) — 2026-05-14 (NMi)",
        sections=[NoteSection("CONTACT", "x")],
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
        angles=UltrasonicAngles(angles=["lidding"]),
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
# Batch mode                                                             #
# --------------------------------------------------------------------- #


def _fake_lead(record_id: str, name: str):
    from scripts.sf import client as sf_client

    return sf_client.LeadRow(
        record_id=record_id,
        name=name,
        source_url="https://acme.lightning.force.com/lightning/o/Lead/list",
    )


def _fake_enrichment(tmp_path):
    from scripts.output.note_renderer import NoteSection, RenderedNote
    from scripts.research.claude_angles import UltrasonicAngles
    from scripts.research.perplexity import PerplexityResearch
    from scripts.sf import enrich_lead

    return enrich_lead.EnrichmentResult(
        note=RenderedNote(
            title="t",
            sections=[NoteSection("CONTACT", "x")],
            enriched_at_utc="2026-05-21T10:30:00Z",
        ),
        docx_path=tmp_path / "note.docx",
        perplexity=PerplexityResearch(summary="s", citations=[], raw_text=""),
        linkedin=None,
        angles=UltrasonicAngles(angles=["a"]),
    )


def test_batch_iterates_all_leads_and_aggregates_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    """--batch enriches every fetched lead and aggregates counters.

    Three leads in → leads_evaluated=3 and docx_written=3 in the
    single-line summary. Each lead gets its own _run_enrichment call.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {"inputs": {"salesforce_org_url": "https://acme.lightning.force.com"}}
        )
    )

    leads = [
        _fake_lead("00Q000000000001", "Lead One"),
        _fake_lead("00Q000000000002", "Lead Two"),
        _fake_lead("00Q000000000003", "Lead Three"),
    ]
    enrich_calls: list = []
    monkeypatch.setattr(agent, "_run_batch_fetch", lambda **k: leads)

    def fake_enrichment(**kwargs):
        enrich_calls.append(kwargs["lead"].record_id)
        return _fake_enrichment(tmp_path)

    monkeypatch.setattr(agent, "_run_enrichment", fake_enrichment)

    rc = agent.main(
        [
            "--command", "run", "--dry-run",
            "--batch", "--max-leads", "10",
            "--config", str(cfg),
        ]
    )

    assert rc == 0
    assert enrich_calls == [
        "00Q000000000001",
        "00Q000000000002",
        "00Q000000000003",
    ]
    out = capsys.readouterr().out
    summary = next(
        ln for ln in out.splitlines()
        if ln.startswith("pk-lead-intelligence run:")
    )
    assert "leads_evaluated=3" in summary
    assert "docx_written=3" in summary
    assert "leads_failed=0" in summary


def test_batch_continues_after_per_lead_enrichment_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    """One lead's exception does not abort the batch.

    Lead 2 of 3 raises during enrichment; leads 1 and 3 still complete.
    leads_failed=1 surfaces in the summary so the operator can audit.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {"inputs": {"salesforce_org_url": "https://acme.lightning.force.com"}}
        )
    )

    leads = [
        _fake_lead("00Q000000000001", "Lead One"),
        _fake_lead("00Q000000000002", "Lead Two"),
        _fake_lead("00Q000000000003", "Lead Three"),
    ]
    monkeypatch.setattr(agent, "_run_batch_fetch", lambda **k: leads)

    def flaky_enrichment(**kwargs):
        if kwargs["lead"].record_id == "00Q000000000002":
            raise RuntimeError("simulated Perplexity 500")
        return _fake_enrichment(tmp_path)

    monkeypatch.setattr(agent, "_run_enrichment", flaky_enrichment)

    rc = agent.main(
        [
            "--command", "run", "--dry-run",
            "--batch", "--max-leads", "10",
            "--config", str(cfg),
        ]
    )

    assert rc == 0
    captured = capsys.readouterr()
    summary = next(
        ln for ln in captured.out.splitlines()
        if ln.startswith("pk-lead-intelligence run:")
    )
    assert "leads_evaluated=3" in summary
    assert "docx_written=2" in summary
    assert "leads_failed=1" in summary
    # The failing lead's record id should be surfaced on stderr so the
    # operator can investigate without grepping a debug log.
    assert "00Q000000000002" in captured.err


def test_batch_renders_failed_leads_block_on_stdout_for_non_technical_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    """Per-lead failures must be visible in the operator's terminal.

    Issue #774: a non-technical operator running the skill in a Seren
    Desktop terminal cannot grep stderr. The summary line on stdout says
    `leads_failed=N` but says nothing about which leads or why. Failures
    must render in a structured block on stdout right before the summary
    so the same output stream the operator is already reading carries
    everything they need to act on.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {"inputs": {"salesforce_org_url": "https://acme.lightning.force.com"}}
        )
    )

    leads = [
        _fake_lead("00Q000000000001", "Lead One"),
        _fake_lead("00Q000000000002", "Lead Two"),
        _fake_lead("00Q000000000003", "Lead Three"),
    ]
    monkeypatch.setattr(agent, "_run_batch_fetch", lambda **k: leads)

    def flaky_enrichment(**kwargs):
        if kwargs["lead"].record_id == "00Q000000000002":
            raise RuntimeError("simulated Perplexity 500")
        return _fake_enrichment(tmp_path)

    monkeypatch.setattr(agent, "_run_enrichment", flaky_enrichment)

    agent.main(
        [
            "--command", "run", "--dry-run",
            "--batch", "--max-leads", "10",
            "--config", str(cfg),
        ]
    )

    out = capsys.readouterr().out
    # Operator-facing header includes the failure count.
    assert "FAILED LEADS (1):" in out
    # Each failure block names the lead and the exception.
    assert "00Q000000000002" in out
    assert "Lead Two" in out
    assert "RuntimeError" in out
    assert "simulated Perplexity 500" in out
    # Block appears BEFORE the cron-parseable summary line so the
    # operator reads context before result.
    failed_idx = out.index("FAILED LEADS")
    summary_idx = out.index("pk-lead-intelligence run:")
    assert failed_idx < summary_idx


# --------------------------------------------------------------------- #
# Bug 3 — operator-readable summary on zero-row case (issue #776)        #
# --------------------------------------------------------------------- #


def test_batch_zero_leads_renders_summary_not_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    """Empty list view must render a structured summary, not a stack trace.

    Operator scenario: the batch source loads but every row is a Lightning
    placeholder (FLS gap on Lead.Name). The source reader raises
    ZeroLeadsFoundError. main() must catch, print a LEAD LIST IS EMPTY
    block with the diagnostic message, print the cron-parseable summary
    with leads_evaluated=0 leads_failed=0, and return 0. A Python
    traceback in this path breaks the cron summary contract and gives
    a non-technical operator nothing to act on.
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {"inputs": {"salesforce_org_url": "https://acme.lightning.force.com"}}
        )
    )

    from scripts.sf import client as sf_client

    def fake_fetch(**_kwargs):
        raise sf_client.ZeroLeadsFoundError(
            "No Lead row with a readable name found in the list view "
            "(rows scanned=47, missing href=0, blank text=0, "
            "Lightning placeholder `[[…]]`=47, non-Lead record (`005`/etc.)=0). "
            "If placeholder count is non-zero, the running SSO user likely "
            "lacks field-level read access to Lead.Name — escalate to the "
            "Salesforce admin."
        )

    monkeypatch.setattr(agent, "_run_batch_fetch", fake_fetch)

    rc = agent.main(
        [
            "--command", "run", "--dry-run",
            "--batch", "--max-leads", "10",
            "--config", str(cfg),
        ]
    )

    assert rc == 0
    out = capsys.readouterr().out
    # Operator-readable block names the zero state and surfaces the
    # diagnostic message verbatim so the FLS-gap hint reaches the operator.
    assert "LEAD LIST IS EMPTY" in out
    assert "Lightning placeholder" in out
    assert "field-level read access" in out
    # Summary contract preserved.
    summary = next(
        ln for ln in out.splitlines()
        if ln.startswith("pk-lead-intelligence run:")
    )
    assert "leads_evaluated=0" in summary
    assert "leads_failed=0" in summary


# --------------------------------------------------------------------- #
# Bug 4 — --batch --allow-live single-Playwright dispatch (issue #776)   #
# --------------------------------------------------------------------- #


def test_batch_live_dispatches_to_single_session_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    """--batch --allow-live routes to _run_batch_live, not the per-lead loop.

    Before this fix, --batch --allow-live ran _run_batch_fetch (one
    browser launch) then _run_live_note_write per lead (N more browser
    launches) = N+1 Chromium cold-launches per cycle. The fix moves all
    --batch --allow-live work into _run_batch_live which holds one
    Playwright session for the whole batch. The dispatcher test pins
    the routing; the single-session property is a structural invariant
    of _run_batch_live's implementation.

    This test asserts:
    - --batch --allow-live calls _run_batch_live (and not the per-lead
      _run_live_note_write inside main).
    - --batch --dry-run still calls _run_batch_fetch (single-session is
      a live-only concern; dry-run has no per-lead Playwright work).
    """

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "inputs": {
                    "salesforce_org_url": "https://acme.lightning.force.com",
                    "live_mode": True,
                    "serendb_connection_uri": "postgres://fake",
                }
            }
        )
    )

    from scripts.storage import enrichment_ledger

    class _FakeLedger:
        def ensure_schema(self):
            pass

        def was_recently_enriched(self, *_args, **_kwargs):
            return False

        def record_enrichment(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(
        enrichment_ledger,
        "PsycopgEnrichmentLedger",
        lambda *_a, **_k: _FakeLedger(),
    )

    batch_live_called = []
    batch_fetch_called = []
    live_note_write_called = []

    def fake_batch_live(**kwargs):
        batch_live_called.append(kwargs)
        return agent._BatchLiveResult(
            leads_evaluated=0,
            counters={
                "notes_written": 0,
                "notes_skipped_non_pk": 0,
                "notes_skipped_recent": 0,
                "docx_written": 0,
                "leads_failed": 0,
            },
            failures=[],
        )

    monkeypatch.setattr(agent, "_run_batch_live", fake_batch_live)
    monkeypatch.setattr(
        agent, "_run_batch_fetch",
        lambda **k: batch_fetch_called.append(k) or [],
    )
    monkeypatch.setattr(
        agent, "_run_live_note_write",
        lambda **k: live_note_write_called.append(k) or None,
    )

    # --batch --allow-live → _run_batch_live, never the per-lead path
    rc = agent.main(
        [
            "--command", "run", "--allow-live",
            "--batch", "--max-leads", "10",
            "--config", str(cfg),
        ]
    )
    assert rc == 0
    assert len(batch_live_called) == 1
    assert batch_fetch_called == []
    assert live_note_write_called == []

    # --batch --dry-run still uses _run_batch_fetch (no behavior change)
    batch_live_called.clear()
    batch_fetch_called.clear()
    rc = agent.main(
        [
            "--command", "run", "--dry-run",
            "--batch", "--max-leads", "10",
            "--config", str(cfg),
        ]
    )
    assert rc == 0
    assert batch_live_called == []
    assert len(batch_fetch_called) == 1


def test_live_batch_candidate_source_uses_pk_report_not_all_open_leads(
    monkeypatch: pytest.MonkeyPatch,
):
    """Live batch candidates must come from the pinned PK report.

    Issue #838: `--batch --allow-live` was sourcing the generic
    AllOpenLeads list view, then paying enrichment cost for non-PK rows
    that the write gate later rejected. The production live candidate
    seam must use the PK-filtered All Sources PK Leads report instead.
    """

    calls: list[str] = []
    expected = [_fake_lead("00Q000000000001", "PK Lead")]

    def fake_pk_report_fetch(**_kwargs):
        calls.append("pk_report")
        return expected

    def fail_open_leads(**_kwargs):
        raise AssertionError("live batch must not source AllOpenLeads")

    monkeypatch.setattr(
        agent.sf_client, "fetch_all_sources_pk_leads", fake_pk_report_fetch
    )
    monkeypatch.setattr(agent.sf_client, "fetch_open_leads", fail_open_leads)

    result = agent._fetch_live_batch_candidates(
        page=object(),
        salesforce_org_url="https://acme.lightning.force.com",
        limit=10,
    )

    assert result == expected
    assert calls == ["pk_report"]


# --------------------------------------------------------------------- #
# ContentNote throttle (issue #783)                                     #
# --------------------------------------------------------------------- #


def test_iterate_batch_writes_skips_non_pk_before_enrichment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Non-PK rows must not trigger Perplexity/Claude/docx work.

    The live write gate already prevented bad Salesforce Notes. The bug
    in #838 was earlier in the flow: batch mode enriched non-PK rows
    before discovering they were not Packaging. The PK detail-page gate
    must run first so non-PK candidates are cheap skips.
    """

    from dataclasses import replace

    from scripts.sf import client as sf_client
    from scripts.sf import write_note

    non_pk = _fake_lead("00Q000000000001", "Non PK")
    pk = _fake_lead("00Q000000000002", "PK")
    enrich_calls: list[str] = []
    write_calls: list[str] = []

    def fake_populate(**kwargs):
        lead = kwargs["lead"]
        return replace(lead, is_packaging=(lead.record_id == pk.record_id))

    def fake_enrichment(**kwargs):
        lead = kwargs["lead"]
        enrich_calls.append(lead.record_id)
        return _fake_enrichment(tmp_path)

    def fake_write(**kwargs):
        write_calls.append(kwargs["options"].lead.record_id)
        return write_note.NoteWriteResult(
            status="written", last_enriched_at="2026-05-21T10:30:00Z"
        )

    monkeypatch.setattr(sf_client, "populate_is_packaging", fake_populate)
    monkeypatch.setattr(agent, "_run_enrichment", fake_enrichment)
    monkeypatch.setattr(write_note, "write_note_to_lead", fake_write)

    counters, failures = agent._iterate_batch_writes(
        leads=[non_pk, pk],
        page=object(),
        salesforce_org_url="https://acme.lightning.force.com",
        output_dir=tmp_path,
        ledger=object(),
        scrape_fn=None,
        linkedin_scrape_min_confidence=70,
        pause_between_notes_seconds=90,
        sleep_fn=lambda _seconds: None,
    )

    assert failures == []
    assert enrich_calls == [pk.record_id]
    assert write_calls == [pk.record_id]
    assert counters["notes_skipped_non_pk"] == 1
    assert counters["notes_written"] == 1
    assert counters["docx_written"] == 1


def test_iterate_batch_writes_sleeps_pause_seconds_only_between_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Salesforce ContentNote enforces a ~90s window between sequential
    Note writes on the same Lightning session. The batch loop must pause
    `pause_between_notes_seconds` after every successful write — but
    only when more leads remain, and only after a *write* (skips and
    failures did not consume the throttle slot).

    Single critical test, two scenarios in one body:

    Scenario A — 4 writes succeed in order. Expect exactly 3 sleep
        calls (after leads 1, 2, 3 — never after the last lead).
        Each call argument equals the configured pause.

    Scenario B — 4 leads, lead #2 returns `skipped_recent` (no
        ContentNote write). Expect exactly 2 sleep calls (after
        leads 1 and 3). Lead #2 must not trigger a sleep, because
        no throttle slot was consumed.

    The helper signature lets the test inject `sleep_fn` so we can
    record calls without actually blocking.
    """

    from dataclasses import replace

    from scripts.sf import client as sf_client
    from scripts.sf import write_note

    leads = [_fake_lead(f"00Q00000000000{i}", f"Lead {i}") for i in range(1, 5)]

    # Stub enrichment and the SF cross-division populate call so the
    # loop body reaches the write step unchanged.
    monkeypatch.setattr(
        agent, "_run_enrichment", lambda **k: _fake_enrichment(tmp_path)
    )
    monkeypatch.setattr(
        sf_client,
        "populate_is_packaging",
        lambda **k: replace(k["lead"], is_packaging=True),
    )

    # -- Scenario A: every write returns `written` -------------------- #
    monkeypatch.setattr(
        write_note,
        "write_note_to_lead",
        lambda **k: write_note.NoteWriteResult(
            status="written", last_enriched_at="2026-05-21T10:30:00Z"
        ),
    )
    sleeps_a: list[int] = []
    counters_a, failures_a = agent._iterate_batch_writes(
        leads=leads,
        page=object(),
        salesforce_org_url="https://acme.lightning.force.com",
        output_dir=tmp_path,
        ledger=object(),
        scrape_fn=None,
        linkedin_scrape_min_confidence=70,
        pause_between_notes_seconds=90,
        sleep_fn=sleeps_a.append,
    )
    assert counters_a["notes_written"] == 4
    assert failures_a == []
    assert sleeps_a == [90, 90, 90], (
        "4 successful writes must produce exactly 3 inter-write pauses, "
        f"got {sleeps_a!r}"
    )

    # -- Scenario B: lead #2 is skipped_recent ------------------------ #
    def mixed_write(**kwargs):
        lead = kwargs["options"].lead
        if lead.record_id == "00Q000000000002":
            return write_note.NoteWriteResult(
                status="skipped_recent",
                last_enriched_at="2026-05-21T10:30:00Z",
            )
        return write_note.NoteWriteResult(
            status="written", last_enriched_at="2026-05-21T10:30:00Z"
        )

    monkeypatch.setattr(write_note, "write_note_to_lead", mixed_write)
    sleeps_b: list[int] = []
    counters_b, failures_b = agent._iterate_batch_writes(
        leads=leads,
        page=object(),
        salesforce_org_url="https://acme.lightning.force.com",
        output_dir=tmp_path,
        ledger=object(),
        scrape_fn=None,
        linkedin_scrape_min_confidence=70,
        pause_between_notes_seconds=90,
        sleep_fn=sleeps_b.append,
    )
    assert counters_b["notes_written"] == 3
    assert counters_b["notes_skipped_recent"] == 1
    assert failures_b == []
    assert sleeps_b == [90, 90], (
        "Skipped leads do not consume a ContentNote throttle slot, so "
        "no sleep should follow them. Expected 2 sleeps (after leads "
        f"#1 and #3), got {sleeps_b!r}"
    )


# --------------------------------------------------------------------- #
# Headless-by-default contract                                           #
# --------------------------------------------------------------------- #


def test_headless_default_is_true_unless_headful_passed():
    """Default is headless; --headful flips it for SSO debugging.

    Issue: an autonomous batch run shouldn't pop a Chromium window over
    the operator's work every cycle. The first-run SSO-debug case is
    now opt-in via --headful, not opt-out via --headless.
    """

    parser = agent._build_parser()

    default_args = parser.parse_args(["--command", "run", "--dry-run"])
    assert default_args.headless is True

    headful_args = parser.parse_args(
        ["--command", "run", "--dry-run", "--headful"]
    )
    assert headful_args.headless is False


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


# --------------------------------------------------------------------- #
# Weekly run-log persistence (Phase 5 issue #779)                       #
# --------------------------------------------------------------------- #


def _make_share_result():
    """Build a ShareResult that looks like a successful Drive upload."""
    from scripts.integrations import google_drive

    return google_drive.ShareResult(
        status="shared",
        doc_url="https://docs.google.com/document/d/abc",
        shared_with="nathan@example.com",
    )


def _weekly_config(tmp_path: Path, *, live: bool) -> Path:
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "inputs": {
                    "salesforce_org_url": "https://acme.lightning.force.com",
                    "live_mode": live,
                    "google_drive_folder_id": "folder123",
                    "nathan_share_email": "nathan@example.com",
                    "monthly_close_target_usd": 500000,
                }
            }
        )
    )
    return cfg


def test_weekly_live_appends_to_run_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful live weekly run must persist a record so /pk-status
    has something to read. Without this the slash command always
    reports 'no doc for this week' even after a real publish.
    """

    state_dir = tmp_path / "state"
    cfg = _weekly_config(tmp_path, live=True)

    monkeypatch.setattr(
        agent, "_run_weekly", lambda **k: _make_share_result()
    )
    monkeypatch.setenv("OP_VAULT", "PK Salesforce Skill")
    monkeypatch.setenv("OP_ITEM", "PK Salesforce")

    rc = agent.main(
        [
            "--command", "weekly",
            "--allow-live",
            "--config", str(cfg),
            "--state-dir", str(state_dir),
        ]
    )
    assert rc == 0

    from scripts.storage import weekly_run_log

    latest = weekly_run_log.latest(state_dir)
    assert latest is not None, "live weekly must persist a record"
    assert latest["doc_url"] == "https://docs.google.com/document/d/abc"
    assert latest["status"] == "shared"
    assert latest["shared_with"] == "nathan@example.com"
    # week_label is derived inside agent from now(); just confirm shape.
    assert latest["week_label"].startswith("20")
    assert "-W" in latest["week_label"]


def test_weekly_dry_run_does_not_append_to_run_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run weekly is for the operator to preview the rendered doc.
    It must not leave a fake URL in the log that /pk-status would then
    surface as the latest published doc.
    """

    state_dir = tmp_path / "state"
    cfg = _weekly_config(tmp_path, live=False)

    from scripts.integrations import google_drive

    monkeypatch.setattr(
        agent,
        "_run_weekly",
        lambda **k: google_drive.ShareResult(
            status="dry_run", doc_url=None, shared_with="nathan@example.com"
        ),
    )
    monkeypatch.setenv("OP_VAULT", "PK Salesforce Skill")
    monkeypatch.setenv("OP_ITEM", "PK Salesforce")

    rc = agent.main(
        [
            "--command", "weekly",
            "--dry-run",
            "--config", str(cfg),
            "--state-dir", str(state_dir),
        ]
    )
    assert rc == 0

    from scripts.storage import weekly_run_log

    assert weekly_run_log.latest(state_dir) is None


# --------------------------------------------------------------------- #
# Issue #781 — LinkedIn scraper telemetry on the cron summary line       #
# --------------------------------------------------------------------- #


def test_print_run_summary_includes_linkedin_counters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The cron parses the run-summary line; new counters must appear in
    a stable position so existing awk/grep parses continue to work and
    operators can see scraper health at a glance.
    """

    agent._print_run_summary(
        agent.RunSummary(
            command="run",
            dry_run=False,
            leads_evaluated=3,
            notes_written=2,
            notes_skipped_non_pk=0,
            notes_skipped_recent=1,
            docx_written=3,
            leads_failed=0,
            linkedin_profiles_scraped=2,
            linkedin_signed_out=1,
        )
    )

    out = capsys.readouterr().out
    assert "linkedin_profiles_scraped=2" in out
    assert "linkedin_signed_out=1" in out
    # Existing keys still present in the same line.
    assert "leads_evaluated=3" in out
    assert "notes_written=2" in out
