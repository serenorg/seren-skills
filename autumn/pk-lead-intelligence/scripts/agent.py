"""Top-level entrypoint for pk-lead-intelligence runs.

Phase 4 supports three commands:

    python scripts/agent.py --command run        --dry-run
    python scripts/agent.py --command run        --allow-live   # Phase 4
    python scripts/agent.py --command provision  --dry-run
    python scripts/agent.py --command provision  --allow-live
    python scripts/agent.py --command weekly                    # Phase 4

The `run` command:

1. Reads SF credentials from 1Password via `auth.op_service_account`.
2. Launches a Playwright Chromium with the persisted storage_state
   (if any) and drives Microsoft SSO via `auth.microsoft_sso`.
3. Navigates to the Lead list and reads the first Lead row.
4. Runs the enrichment pipeline (`sf.enrich_lead`) over that lead —
   Perplexity research, LinkedIn discovery, Claude hypothesis — and
   writes a local `.docx` Note for operator review.
5. (Phase 4, `--allow-live`) Populates `is_packaging` from the
   Lead's "Project Business Unit" Details field, then writes the
   rendered Note to the Lead's Related tab gated by
   `is_packaging_lead` + a SerenDB-backed recency ledger
   (`pk_lead_enrichment_log`).

The `provision` command (Phase 3) validates the three
operator-owned Salesforce artifacts the cron reads:

* `All Sources PK Leads` report — filter Project Business Unit = PACKAGING.
* `PK Inbound Web Lead and Activity Tracking - SerenAI` dashboard.
* `PK Inbound Web Lead and Opportunity Tracking - SerenAI` dashboard.

The skill does not create or edit any of these — Nathan owns them
and the skill confirms they still load under the operator's
Salesforce session (issue #563).

The `weekly` command (Phase 4) composes the Tuesday-morning status
doc from the past 7 days of enrichments and uploads it to Google
Drive, shared with `inputs.nathan_share_email`.

Both `run --allow-live` and `provision --allow-live` honor the
defense-in-depth double gate: the CLI flag must be paired with
`inputs.live_mode=true` in config.json. Each run prints a single
parseable summary line so the seren-cron `execution_results`
table can capture the run state.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `from scripts.* import …` work when this file is launched as
# a script (`python scripts/agent.py`), not just as a module
# (`python -m scripts.agent`). Without this, Python puts the
# `scripts/` directory on sys.path[0] and the `scripts` package
# itself is unreachable. The `not in` guard keeps the nudge
# idempotent so pytest and `python -m` paths — which already put
# the skill root on sys.path — don't get a duplicate entry. Issue
# #541.
_SKILL_ROOT = str(Path(__file__).resolve().parent.parent)
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

from dataclasses import dataclass  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from scripts.auth import microsoft_sso  # noqa: E402
from scripts.auth import op_service_account  # noqa: E402
from scripts.integrations import google_drive  # noqa: E402
from scripts.output import weekly_status  # noqa: E402
from scripts.research import claude_hypothesis  # noqa: E402
from scripts.research import linkedin_search  # noqa: E402
from scripts.research import perplexity  # noqa: E402
from scripts.sf import build_all_sources_leads_report as all_leads_report  # noqa: E402
from scripts.sf import build_pk_lead_dashboard as lead_dashboard  # noqa: E402
from scripts.sf import build_pk_opp_artifacts as opp_artifacts  # noqa: E402
from scripts.sf import client as sf_client  # noqa: E402
from scripts.sf import enrich_lead  # noqa: E402
from scripts.sf import write_note  # noqa: E402
from scripts.storage import enrichment_ledger  # noqa: E402


# --------------------------------------------------------------------- #
# Config loading                                                        #
# --------------------------------------------------------------------- #


def _load_config(config_path: Path) -> dict:
    """Read and parse `config.json`. Caller decides what to do on miss.

    Kept deliberately minimal — Phase 3+ will likely introduce a
    pydantic model. For Phase 2 the only field we read is
    `inputs.salesforce_org_url`.
    """

    if not config_path.exists():
        raise FileNotFoundError(
            f"config.json not found at {config_path}. "
            "Copy config.example.json to config.json and fill in "
            "inputs.salesforce_org_url before running."
        )
    return json.loads(config_path.read_text())


def _resolve_salesforce_org_url(config: dict) -> str:
    """Pull the SF org URL out of config with a clear error on missing.

    Defaulting is intentionally not supported — different operators
    drive different orgs and a silent fallback would be a bug.
    """

    url = (config.get("inputs") or {}).get("salesforce_org_url", "")
    if not url:
        raise ValueError(
            "config.json: inputs.salesforce_org_url is empty. "
            "Set it to the live org URL "
            "(e.g. https://acme.lightning.force.com)."
        )
    # `<` is not a legal URL host character. Its presence anywhere in
    # the value means the operator left the example placeholder
    # (e.g. `https://<org-subdomain>.lightning.force.com`) in place.
    if "<" in url:
        raise ValueError(
            "config.json: inputs.salesforce_org_url is still the "
            "example placeholder. Replace `<...>` with the live org "
            "subdomain (e.g. https://acme.lightning.force.com)."
        )
    return url


# --------------------------------------------------------------------- #
# CLI                                                                   #
# --------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pk-lead-intelligence",
        description=(
            "Drive Salesforce Lightning as the named human owner, "
            "enrich PK Leads, write Notes, and publish a weekly status "
            "doc. Phase 2 implements the dry-run lead read plus the "
            "enrichment pipeline that produces a local .docx Note."
        ),
    )
    parser.add_argument(
        "--command",
        required=True,
        choices=["run", "provision", "weekly"],
        help=(
            "Top-level command. `run` enriches a Lead (dry-run or "
            "live Note write). `provision` validates the three "
            "operator-owned Salesforce artifacts are reachable. "
            "`weekly` composes the Tuesday-morning status doc and "
            "uploads it to Google Drive. Live commands require "
            "`--allow-live` paired with config `inputs.live_mode=true`."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Plan-only mode. For `run` this disables the live "
            "Note-write path. For `provision` this surfaces the "
            "pinned artifact URLs without navigating to them. For "
            "`weekly` this prints the rendered doc body without "
            "uploading."
        ),
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help=(
            "Enables live Salesforce or Drive writes; must be "
            "paired with `inputs.live_mode=true` in config.json. "
            "For `run`, writes the Note to the Lead's Related tab. "
            "For `provision`, navigates to the three pinned "
            "artifact URLs and confirms they load. For `weekly`, "
            "uploads the doc to Drive."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.json"),
        help="Path to config.json. Defaults to ./config.json.",
    )
    parser.add_argument(
        "--storage-path",
        type=Path,
        default=microsoft_sso.DEFAULT_STORAGE_PATH,
        help=(
            "Path to Playwright storage_state JSON. Defaults to "
            "state/playwright_storage.json (gitignored)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help=(
            "Directory for dry-run .docx Notes. Defaults to ./output "
            "(gitignored)."
        ),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help=(
            "Run Chromium headless. Default is headful so the operator "
            "can watch the first SSO dance and patch selectors if "
            "Microsoft has rotated the UI."
        ),
    )
    return parser


# --------------------------------------------------------------------- #
# Phase 2 dry-run flow                                                  #
# --------------------------------------------------------------------- #


def _run_dry_run(
    *,
    salesforce_org_url: str,
    storage_path: Path,
    headless: bool,
    op_vault: str,
    op_item: str,
) -> sf_client.LeadRow:
    """Drive the full op → SSO → first-lead flow once.

    Returns the LeadRow so callers (CLI, tests, future cron) can
    log or pretty-print it as they please. Lifecycles the
    Playwright browser inside this function; callers do not need to
    manage it.

    This function intentionally does NOT run enrichment — the
    enrichment pipeline does not need the Playwright browser, and
    keeping the Playwright lifecycle isolated to this function means
    the browser closes before any HTTP-bound publisher call runs.
    """

    creds = op_service_account.read_salesforce_credentials(
        vault=op_vault, item=op_item
    )

    # Import inside the function so unit tests can stub the module
    # without needing Playwright installed in the test environment.
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            # Reuse persisted storage if present. Playwright's
            # `new_context(storage_state=...)` is happy to read a
            # file at the given path; we only pass it when the file
            # exists.
            if storage_path.exists():
                context = browser.new_context(storage_state=str(storage_path))
            else:
                context = browser.new_context()
            try:
                result = microsoft_sso.authenticate(
                    context=context,
                    salesforce_org_url=salesforce_org_url,
                    creds=creds,
                    storage_path=storage_path,
                )
                return sf_client.fetch_first_lead(
                    page=result.page,
                    salesforce_org_url=salesforce_org_url,
                )
            finally:
                context.close()
        finally:
            browser.close()


# --------------------------------------------------------------------- #
# Phase 3 provision flow                                                #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProvisionSummary:
    """Aggregate result of one `--command provision` cycle.

    Phase 3 was simplified by issue #563 — the three Salesforce
    artifacts are operator-owned, not skill-provisioned. The
    provision command now just validates each artifact is reachable
    under the operator's session, so the summary is the three
    navigate-only results.
    """

    all_sources_report: all_leads_report.ReportResult
    lead_dashboard: lead_dashboard.DashboardResult
    opp_dashboard: opp_artifacts.DashboardResult


def _run_provision(
    *,
    salesforce_org_url: str,
    storage_path: Path,
    headless: bool,
    op_vault: str,
    op_item: str,
    dry_run: bool,
) -> ProvisionSummary:  # pragma: no cover
    """Drive the SSO → validate-three-artifacts flow.

    Lifecycles the Playwright browser inside this function. Each
    sub-validator just navigates to a pinned artifact URL and
    confirms it loads; nothing is created or edited.

    Marked `pragma: no cover` because the live behaviour requires
    Playwright + a real Salesforce org. Tests monkeypatch this
    seam to assert the CLI print contract.
    """

    creds = op_service_account.read_salesforce_credentials(
        vault=op_vault, item=op_item
    )

    # Lazy import — same reasoning as `_run_dry_run`.
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            if storage_path.exists():
                context = browser.new_context(storage_state=str(storage_path))
            else:
                context = browser.new_context()
            try:
                sso_result = microsoft_sso.authenticate(
                    context=context,
                    salesforce_org_url=salesforce_org_url,
                    creds=creds,
                    storage_path=storage_path,
                )
                page = sso_result.page

                report_result = (
                    all_leads_report.build_all_sources_pk_leads_report(
                        page=page, dry_run=dry_run
                    )
                )
                lead_dash_result = lead_dashboard.build_pk_lead_dashboard(
                    page=page, dry_run=dry_run
                )
                opp_dash_result = opp_artifacts.build_pk_opp_dashboard(
                    page=page, dry_run=dry_run
                )

                return ProvisionSummary(
                    all_sources_report=report_result,
                    lead_dashboard=lead_dash_result,
                    opp_dashboard=opp_dash_result,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _print_provision_summary(summary: ProvisionSummary, *, dry_run: bool) -> None:
    """Render the provision summary to stdout.

    Output format is structured so a Phase 5 cron can parse it
    line-by-line. The operator-facing information is the three
    pinned artifact URLs + their validation status.
    """

    verb = "planned" if dry_run else "actioned"
    print(f"provision_summary ({verb}):")

    print(f"  all_sources_report: {summary.all_sources_report.spec.title}")
    print(f"    url: {summary.all_sources_report.url}")
    print(f"    status: {summary.all_sources_report.status}")

    print(f"  lead_dashboard: {summary.lead_dashboard.spec.title}")
    print(
        f"    components: "
        f"{len(summary.lead_dashboard.spec.components)}"
    )
    print(f"    url: {summary.lead_dashboard.url}")
    print(f"    status: {summary.lead_dashboard.status}")

    print(f"  opp_dashboard: {summary.opp_dashboard.spec.title}")
    print(
        f"    components: "
        f"{len(summary.opp_dashboard.spec.components)}"
    )
    print(f"    url: {summary.opp_dashboard.url}")
    print(f"    status: {summary.opp_dashboard.status}")


# --------------------------------------------------------------------- #
# Phase 2 enrichment flow                                               #
# --------------------------------------------------------------------- #


def _run_enrichment(
    *,
    lead: sf_client.LeadRow,
    output_dir: Path,
) -> enrich_lead.EnrichmentResult:
    """Run the research → render → docx pipeline for one Lead.

    Constructs the live `Dependencies` bundle from the published
    adapter functions and delegates to `enrich_lead.enrich`. Tests
    monkeypatch this seam to avoid network and python-docx.

    `company_hint` is `None` in Phase 2 — `LeadRow` does not yet
    carry an explicit company field, and the Perplexity / LinkedIn
    adapters degrade gracefully without it. Phase 3 will populate
    the hint from the All Sources PK Leads report column.
    """

    deps = enrich_lead.Dependencies(
        perplexity_research=perplexity.research_lead,
        linkedin_discover=linkedin_search.discover_candidates,
        claude_hypothesis=claude_hypothesis.generate,
    )
    return enrich_lead.enrich(
        lead=lead,
        deps=deps,
        company_hint=None,
        output_dir=output_dir,
    )


# --------------------------------------------------------------------- #
# Phase 4 live Note write                                               #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunSummary:
    """One-line cron contract for the `run` command.

    The Phase 5 cron parses this line out of stdout and records it
    in the seren-cron `execution_results` table. The format is a
    space-separated `key=value` list prefixed with
    `pk-lead-intelligence run:` — keys must not be renamed without
    bumping the parser.
    """

    command: str
    dry_run: bool
    leads_evaluated: int
    notes_written: int
    notes_skipped_non_pk: int
    notes_skipped_recent: int
    docx_written: int


def _run_live_note_write(
    *,
    salesforce_org_url: str,
    storage_path: Path,
    headless: bool,
    op_vault: str,
    op_item: str,
    enrichment: enrich_lead.EnrichmentResult,
    lead: sf_client.LeadRow,
    ledger: enrichment_ledger.EnrichmentLedger,
    dry_run: bool,
) -> write_note.NoteWriteResult:  # pragma: no cover
    """Drive the Phase 4 Note-write step.

    Two Playwright passes on the same browser context:

    1. `populate_is_packaging` navigates to the Lead detail page,
       reads `Project Business Unit`, and returns a LeadRow whose
       `is_packaging` is True iff the value is "PACKAGING". This is
       the cross-division gate (P0 per SKILL.md) — performed before
       any write attempt so a non-PK Lead never reaches the Note
       form at all.

    2. `write_note_to_lead` runs the recency-and-write path against
       the populated lead, using the injected SerenDB ledger as the
       recency oracle.

    Marked `pragma: no cover` — live correctness is validated at the
    Phase 4 operator checkpoint. Tests monkeypatch this seam.
    """

    creds = op_service_account.read_salesforce_credentials(
        vault=op_vault, item=op_item
    )

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            if storage_path.exists():
                context = browser.new_context(storage_state=str(storage_path))
            else:
                context = browser.new_context()
            try:
                sso_result = microsoft_sso.authenticate(
                    context=context,
                    salesforce_org_url=salesforce_org_url,
                    creds=creds,
                    storage_path=storage_path,
                )
                page = sso_result.page

                populated_lead = sf_client.populate_is_packaging(
                    page=page,
                    lead=lead,
                    salesforce_org_url=salesforce_org_url,
                )

                return write_note.write_note_to_lead(
                    page=page,
                    options=write_note.NoteWriteOptions(
                        lead=populated_lead,
                        note=enrichment.note,
                        salesforce_org_url=salesforce_org_url,
                    ),
                    now=datetime.now(tz=timezone.utc),
                    dry_run=dry_run,
                    ledger=ledger,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _print_run_summary(summary: RunSummary) -> None:
    """Emit the single-line cron-contract summary to stdout.

    Booleans render as lowercase `true`/`false` so the line is
    grep-friendly. Keys are listed in a stable order so a flat
    `awk`/`grep` parse keeps working when columns are added later.
    """

    parts = [
        f"command={summary.command}",
        f"dry_run={'true' if summary.dry_run else 'false'}",
        f"leads_evaluated={summary.leads_evaluated}",
        f"notes_written={summary.notes_written}",
        f"notes_skipped_non_pk={summary.notes_skipped_non_pk}",
        f"notes_skipped_recent={summary.notes_skipped_recent}",
        f"docx_written={summary.docx_written}",
    ]
    print(f"pk-lead-intelligence run: {' '.join(parts)}")


# --------------------------------------------------------------------- #
# Phase 4 weekly status doc                                             #
# --------------------------------------------------------------------- #


def _run_weekly(  # pragma: no cover
    *,
    config: dict,
    dry_run: bool,
) -> google_drive.ShareResult:
    """Compose the weekly status doc and upload it to Drive.

    `pragma: no cover` because the live wiring needs the
    `google-drive` publisher. Tests monkeypatch this seam to
    exercise the CLI; the renderer and the share gate are covered
    directly in their own test files.

    Phase 4 ships the empty-week skeleton: the lead summaries are
    sourced from the SerenDB `enriched_leads` ledger when Phase 4's
    persistence lands. Until then this returns an empty week so the
    operator can rehearse the share path end-to-end.
    """

    from scripts.seren_client import call_publisher  # noqa: PLC0415

    inputs = config.get("inputs") or {}
    folder_id = inputs.get("google_drive_folder_id", "")
    share_email = inputs.get("nathan_share_email", "")
    monthly_target = int(inputs.get("monthly_close_target_usd", 500_000))

    now = datetime.now(tz=timezone.utc)
    week_label = f"{now.year}-W{now.isocalendar().week:02d}"

    doc = weekly_status.compose_weekly_status_doc(
        week_label=week_label,
        lead_summaries=[],
        monthly_close_target_usd=monthly_target,
    )

    # Adapt the shared `call_publisher(publisher, method, path, *, body)`
    # surface to the narrower `(publisher, path, body) -> dict` shape
    # `google_drive.upload_and_share` expects. Drive writes are
    # always POST in this path.
    def _post(publisher: str, path: str, body: dict) -> dict:
        return call_publisher(publisher, "POST", path, body=body)

    return google_drive.upload_and_share(
        doc=doc,
        folder_id=folder_id,
        share_email=share_email,
        dry_run=dry_run,
        publisher_call=_post,
    )


def _resolve_live_mode(config: dict) -> bool:
    """Read `inputs.live_mode` from config with a safe False default.

    Defense-in-depth: the CLI also requires `--allow-live` on live
    paths. A missing field, a `null` value, or a `"true"` string is
    all treated as False — the gate accepts only the JSON literal
    `true` (parsed to Python's `True` singleton). This makes a
    typo in config.json fail safely closed.
    """

    value = (config.get("inputs") or {}).get("live_mode", False)
    return value is True


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns a process exit code."""

    args = _build_parser().parse_args(argv)

    # `run` command — Phase 2 dry-run + Phase 4 live Note write.
    # `--dry-run` is required when `--allow-live` is absent (we
    # never enrich silently without operator opt-in). When
    # `--allow-live` is set, the config gate must also be set.
    if args.command == "run":
        if args.allow_live and args.dry_run:
            print(
                "--allow-live and --dry-run are mutually exclusive. "
                "Pick one and re-run.",
                file=sys.stderr,
            )
            return 2
        if not args.allow_live and not args.dry_run:
            print(
                "--command run requires --dry-run or --allow-live. "
                "Re-run with one of the two flags.",
                file=sys.stderr,
            )
            return 2
        if args.allow_live:
            config = _load_config(args.config)
            if not _resolve_live_mode(config):
                print(
                    "--allow-live requires config.json "
                    "`inputs.live_mode: true`. Both gates must be set; "
                    "neither alone is sufficient.",
                    file=sys.stderr,
                )
                return 2

    # `provision` command (Phase 3) — --allow-live is honored when
    # paired with config `inputs.live_mode=true`. Either gate
    # alone refuses to drive the UI.
    if args.command == "provision":
        if args.allow_live and args.dry_run:
            print(
                "--allow-live and --dry-run are mutually exclusive. "
                "Pick one and re-run.",
                file=sys.stderr,
            )
            return 2
        if args.allow_live:
            config = _load_config(args.config)
            if not _resolve_live_mode(config):
                print(
                    "--allow-live requires config.json "
                    "`inputs.live_mode: true`. Both gates must be set; "
                    "neither alone is sufficient.",
                    file=sys.stderr,
                )
                return 2

    # `weekly` command (Phase 4) — same double-gate as the others.
    if args.command == "weekly":
        if args.allow_live and args.dry_run:
            print(
                "--allow-live and --dry-run are mutually exclusive. "
                "Pick one and re-run.",
                file=sys.stderr,
            )
            return 2
        if args.allow_live:
            config = _load_config(args.config)
            if not _resolve_live_mode(config):
                print(
                    "--allow-live requires config.json "
                    "`inputs.live_mode: true`. Both gates must be set; "
                    "neither alone is sufficient.",
                    file=sys.stderr,
                )
                return 2

    config = _load_config(args.config)
    salesforce_org_url = _resolve_salesforce_org_url(config)

    # OP_VAULT / OP_ITEM come from `.env` via shell export. We read
    # them via os.environ rather than re-parsing `.env` because the
    # Service Account token path already requires the same env to be
    # exported anyway — see scripts/auth/op_service_account.py.
    import os  # noqa: PLC0415 — kept local since `os` is otherwise unused

    op_vault = os.environ.get("OP_VAULT", "PK Salesforce Skill")
    op_item = os.environ.get("OP_ITEM", "PK Salesforce")

    if args.command == "provision":
        summary = _run_provision(
            salesforce_org_url=salesforce_org_url,
            storage_path=args.storage_path,
            headless=args.headless,
            op_vault=op_vault,
            op_item=op_item,
            dry_run=args.dry_run,
        )
        _print_provision_summary(summary, dry_run=args.dry_run)
        return 0

    if args.command == "weekly":
        result = _run_weekly(config=config, dry_run=args.dry_run)
        print(
            f"weekly: status={result.status} "
            f"shared_with={result.shared_with or '(none)'} "
            f"url={result.doc_url or '(none)'}"
        )
        return 0

    # args.command == "run"
    lead = _run_dry_run(
        salesforce_org_url=salesforce_org_url,
        storage_path=args.storage_path,
        headless=args.headless,
        op_vault=op_vault,
        op_item=op_item,
    )

    print("first_lead:")
    print(f"  record_id: {lead.record_id}")
    print(f"  name:      {lead.name}")
    print(f"  source:    {lead.source_url}")

    enrichment = _run_enrichment(lead=lead, output_dir=args.output_dir)

    print("enrichment:")
    print(f"  docx:      {enrichment.docx_path}")
    if enrichment.linkedin is not None:
        print(
            f"  linkedin:  {enrichment.linkedin.url} "
            f"({enrichment.linkedin.match_confidence}%)"
        )
    else:
        print("  linkedin:  (no candidate)")
    print(f"  hypothesis: {enrichment.hypothesis.text[:120]}")

    # Phase 4 live Note write. Only runs when --allow-live is set
    # AND the config gate is on (already validated above).
    #
    # `is_packaging` is populated from the Lead detail page's
    # "Project Business Unit" field by `populate_is_packaging`
    # inside `_run_live_note_write` — the cross-division gate
    # (P0 per SKILL.md) fires before any write attempt.
    #
    # Recency is enforced via a SerenDB-backed
    # `pk_lead_enrichment_log` table (issue #563). The operator
    # supplies the connection URI in `inputs.serendb_connection_uri`
    # — Phase 5 will resolve it from the seren-db publisher
    # automatically. Live path fails closed when the URI is missing.
    note_status = "not_attempted"
    if args.allow_live:
        ledger_uri = (config.get("inputs") or {}).get(
            "serendb_connection_uri", ""
        )
        if not ledger_uri:
            print(
                "--allow-live requires config.json "
                "`inputs.serendb_connection_uri` to be set. The "
                "recency ledger lives in SerenDB; without a "
                "connection URI the cron cannot enforce the 24h "
                "skip gate. See SKILL.md > Phase 4.",
                file=sys.stderr,
            )
            return 2
        ledger = enrichment_ledger.PsycopgEnrichmentLedger(ledger_uri)
        ledger.ensure_schema()

        write_result = _run_live_note_write(
            salesforce_org_url=salesforce_org_url,
            storage_path=args.storage_path,
            headless=args.headless,
            op_vault=op_vault,
            op_item=op_item,
            enrichment=enrichment,
            lead=lead,
            ledger=ledger,
            dry_run=False,
        )
        note_status = write_result.status
        print(f"note_write: status={note_status}")

    _print_run_summary(
        RunSummary(
            command="run",
            dry_run=args.dry_run,
            leads_evaluated=1,
            notes_written=1 if note_status == "written" else 0,
            notes_skipped_non_pk=1 if note_status == "skipped_non_pk" else 0,
            notes_skipped_recent=1 if note_status == "skipped_recent" else 0,
            docx_written=1 if enrichment.docx_path else 0,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
