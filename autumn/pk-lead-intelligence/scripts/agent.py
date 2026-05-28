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
3. Reads candidates from the pinned `All Sources PK Leads` report and
   verifies Business Unit -> PACKAGING on each record detail page.
4. Runs the enrichment pipeline (`sf.enrich_lead`) over that lead —
   Perplexity research, LinkedIn discovery, Claude hypothesis — and
   writes a local `.docx` Note for operator review.
5. (Phase 4, `--allow-live`) Populates `is_packaging` from the
   Business Unit -> PACKAGING checkbox on the record detail page,
   then writes the rendered Note to the Lead's Related tab gated by
   `is_packaging_lead` + a SerenDB-backed recency ledger
   (`pk_lead_enrichment_log`).

The `provision` command (Phase 3) validates the three
operator-owned Salesforce artifacts the cron reads:

* `All Sources PK Leads` report — candidate source for daily PK work.
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
import time
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
from typing import Optional  # noqa: E402

from scripts.auth import microsoft_sso  # noqa: E402
from scripts.auth import op_service_account  # noqa: E402
from scripts.integrations import google_drive  # noqa: E402
from scripts.output import weekly_status  # noqa: E402
from scripts.research import claude_angles  # noqa: E402
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
# Config + .env location (issue #848)                                   #
# --------------------------------------------------------------------- #

# Stable, launch-independent user-config dir. Deliberately a *sibling*
# of the synced skill bundle (`~/.config/seren/skills/...`), not inside
# it: the bundle is rewritten on every skill re-sync, which would wipe
# an operator's `config.json` / `.env`. This dir is never managed by
# the sync, so config placed here survives updates.
_STABLE_CONFIG_DIR = Path.home() / ".config" / "seren" / "pk-lead-intelligence"


def _config_search_dirs() -> list[Path]:
    """Directories searched (in order) for `config.json` and `.env`
    when no explicit `--config` is given.

    Precedence: the stable user-config dir (survives skill re-sync) →
    the skill root (the repo working copy) → the current working
    directory (back-compat for the old cwd-relative behaviour).
    """

    return [_STABLE_CONFIG_DIR, Path(_SKILL_ROOT), Path.cwd()]


def _resolve_config_path(cli_config: Optional[Path]) -> Path:
    """Resolve the `config.json` path independent of launch directory.

    An explicit `--config` always wins and is returned verbatim (even
    if missing) so `_load_config` raises a clear error rather than
    silently falling back. Otherwise the first search dir that
    actually contains a `config.json` wins; if none do, the first
    (stable) dir's path is returned so the downstream error names the
    recommended location.
    """

    if cli_config is not None:
        return cli_config
    dirs = _config_search_dirs()
    for directory in dirs:
        candidate = directory / "config.json"
        if candidate.exists():
            return candidate
    return dirs[0] / "config.json"


def _load_config(config_path: Path) -> dict:
    """Read and parse `config.json`. Caller decides what to do on miss.

    Kept deliberately minimal — Phase 3+ will likely introduce a
    pydantic model. For Phase 2 the only field we read is
    `inputs.salesforce_org_url`.
    """

    if not config_path.exists():
        searched = ", ".join(str(d / "config.json") for d in _config_search_dirs())
        raise FileNotFoundError(
            f"config.json not found at {config_path}. "
            "Copy config.example.json to config.json and fill in "
            "inputs.salesforce_org_url before running. "
            f"Searched (in order): {searched}."
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
        default=None,
        help=(
            "Path to config.json. If omitted, the skill searches a "
            "stable, launch-independent set of locations (see "
            "_config_search_dirs): ~/.config/seren/pk-lead-intelligence/, "
            "then the skill root, then the cwd."
        ),
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
        "--state-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "state",
        help=(
            "Directory for skill-local state files. The weekly run log "
            "(`weekly_status_runs.jsonl`, gitignored) lives here and is "
            "read by `slash/pk_status.py`. Defaults to the skill's "
            "state/ directory."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / "Documents" / "pk-lead-intelligence" / "output",
        help=(
            "Directory for dry-run .docx Notes. Defaults to "
            "~/Documents/pk-lead-intelligence/output — kept OUTSIDE the "
            "repo because rendered Notes can contain Salesforce PII "
            "(names, emails, business unit) and this skill ships in a "
            "public repo. The repo's `output/` is gitignored as a "
            "second line of defense, but the default keeps generated "
            "PII off the working tree entirely."
        ),
    )
    parser.add_argument(
        "--headful",
        dest="headless",
        action="store_false",
        default=True,
        help=(
            "Run Chromium with a visible window. Default is headless — "
            "use this only when debugging an SSO cold-start or a "
            "Microsoft / Salesforce selector that has rotated."
        ),
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help=(
            "Iterate every visible open Lead instead of stopping after "
            "the first row. Each Lead runs the same enrich + "
            "(optionally) live-write path as the single-lead flow. "
            "A failure on one Lead does not abort the batch; the "
            "summary line carries `leads_failed=N` for audit. Cap the "
            "batch size with `--max-leads`."
        ),
    )
    parser.add_argument(
        "--max-leads",
        type=int,
        default=50,
        help=(
            "Cap on the number of Leads processed by `--batch`. The cap "
            "exists to bound per-cycle Perplexity + Claude API spend; a "
            "runaway 500-row list cannot burn the operator's budget in "
            "one tick. Ignored without `--batch`."
        ),
    )
    # Issue #783 — Salesforce ContentNote enforces a ~90s window
    # between sequential Note writes on the same Lightning session.
    # Back-to-back writes silently drop after the 2nd-3rd Lead. CLI
    # flag overrides `inputs.pause_between_notes_seconds` in config,
    # which itself defaults to 90 if absent. Set to 0 to disable
    # (only safe when using a Connected App + REST path — not the
    # default UI flow). Use `None` as the argparse sentinel so we can
    # tell "operator passed --pause-between-notes 0" apart from
    # "operator did not pass the flag at all" — the latter should
    # fall through to config / default.
    parser.add_argument(
        "--pause-between-notes",
        type=int,
        default=None,
        dest="pause_between_notes",
        help=(
            "Seconds to wait between sequential live Note writes in "
            "`--batch --allow-live`. Defaults to "
            "`inputs.pause_between_notes_seconds` in config (which "
            "itself defaults to 90). Salesforce ContentNote enforces "
            "a ~90-second cadence on the Lightning UI; without a "
            "pause, batches silently drop Notes after the 2nd-3rd "
            "Lead. Skipped / failed / non-PK Leads do not trigger "
            "the pause (they did not consume a throttle slot)."
        ),
    )
    return parser


# --------------------------------------------------------------------- #
# Phase 2 dry-run flow                                                  #
# --------------------------------------------------------------------- #


def _salesforce_credentials_provider(*, op_vault: str, op_item: str):
    """Build a lazy credentials reader for Microsoft SSO.

    `microsoft_sso.authenticate` tries Playwright storage reuse before
    fresh login. Returning a provider instead of concrete credentials
    prevents valid saved sessions from blocking on 1Password.
    """

    def _read_credentials():
        return op_service_account.read_salesforce_credentials(
            vault=op_vault,
            item=op_item,
        )

    return _read_credentials


def _run_dry_run(
    *,
    salesforce_org_url: str,
    storage_path: Path,
    headless: bool,
    op_vault: str,
    op_item: str,
    candidate_limit: int,
) -> sf_client.LeadRow:
    """Drive the full op → SSO → first Packaging lead flow once.

    Returns the LeadRow so callers (CLI, tests, future cron) can
    log or pretty-print it as they please. Lifecycles the
    Playwright browser inside this function; callers do not need to
    manage it.

    This function intentionally does NOT run enrichment. It only
    selects a Lead that has passed the Business Unit -> PACKAGING
    detail-page gate, then closes the browser before any HTTP-bound
    publisher call runs.
    """

    creds = _salesforce_credentials_provider(op_vault=op_vault, op_item=op_item)

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
                leads = _select_packaging_leads_from_report(
                    page=result.page,
                    salesforce_org_url=salesforce_org_url,
                    limit=candidate_limit,
                )
                return leads[0]
            finally:
                context.close()
        finally:
            browser.close()


def _select_packaging_leads_from_report(
    *,
    page,
    salesforce_org_url: str,
    limit: int,
) -> list[sf_client.LeadRow]:
    """Return PK-report candidates that pass the live Business Unit gate."""

    candidates = sf_client.fetch_all_sources_pk_leads(page=page, limit=limit)
    selected: list[sf_client.LeadRow] = []
    skipped_non_pk = 0

    for lead in candidates:
        populated_lead = sf_client.populate_is_packaging(
            page=page,
            lead=lead,
            salesforce_org_url=salesforce_org_url,
        )
        if enrich_lead.is_packaging_lead(populated_lead):
            selected.append(populated_lead)
        else:
            skipped_non_pk += 1

    if not selected:
        raise sf_client.ZeroLeadsFoundError(
            "No Lead passed the Business Unit -> PACKAGING gate from "
            "the All Sources PK Leads report "
            f"(candidates scanned={len(candidates)}, "
            f"non-PK skipped={skipped_non_pk}). "
            "Confirm the pinned report and Lead detail Business Unit "
            "fields agree before running enrichment."
        )

    return selected


def _run_batch_fetch(
    *,
    salesforce_org_url: str,
    storage_path: Path,
    headless: bool,
    op_vault: str,
    op_item: str,
    limit: int,
) -> list[sf_client.LeadRow]:
    """Drive the op → SSO → PK-report gated leads flow once.

    Mirrors `_run_dry_run`'s Playwright lifecycle but returns up to
    `limit` rows from the pinned All Sources PK Leads report after
    verifying each row on the record detail page. Used by
    `--batch --dry-run`. Enrichment runs against the returned list in
    `main` — this function deliberately stops after selection so the
    browser closes before any Perplexity or Claude call fires.
    """

    creds = _salesforce_credentials_provider(op_vault=op_vault, op_item=op_item)

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
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
                return _select_packaging_leads_from_report(
                    page=result.page,
                    salesforce_org_url=salesforce_org_url,
                    limit=limit,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _fetch_live_batch_candidates(
    *,
    page,
    salesforce_org_url: str,
    limit: int,
) -> list[sf_client.LeadRow]:
    """Fetch the live batch candidate set from the PK-filtered source."""

    return sf_client.fetch_all_sources_pk_leads(page=page, limit=limit)


@dataclass(frozen=True)
class _BatchLiveResult:
    """Return type for `_run_batch_live`.

    `counters` carries the same keys as the dry-run batch loop —
    notes_written, notes_skipped_non_pk, notes_skipped_recent,
    docx_written, leads_failed — so the caller renders the cron summary
    line identically for both batch paths.
    """

    leads_evaluated: int
    counters: dict
    failures: list


def _iterate_batch_writes(
    *,
    leads,
    page,
    salesforce_org_url: str,
    output_dir: Path,
    ledger: enrichment_ledger.EnrichmentLedger,
    scrape_fn,
    linkedin_scrape_min_confidence: int,
    pause_between_notes_seconds: int,
    sleep_fn=time.sleep,
):
    """Per-lead populate_is_packaging → enrich → write-Note loop.

    Pulled out of `_run_batch_live` so the throttle behavior is
    testable without standing up Playwright. The Playwright lifecycle
    stays in `_run_batch_live`; this helper just walks the list and
    delegates to the same primitives.

    Throttle contract (issue #783): after every successful `written`
    result, sleep `pause_between_notes_seconds` BEFORE moving to the
    next Lead — but only when more Leads remain (the final write
    never gets a trailing sleep). Skipped and failed results do NOT
    trigger a sleep — they did not write a Note, so they did not
    consume a Salesforce ContentNote throttle slot.

    `sleep_fn` is injected so tests can record calls without blocking.
    Setting `pause_between_notes_seconds` to 0 disables the pause
    entirely; the no-pause path is the pre-#783 behavior and is only
    safe under a Connected App + REST execution path.

    Returns `(counters, failures)`.
    """

    counters = {
        "notes_written": 0,
        "notes_skipped_non_pk": 0,
        "notes_skipped_recent": 0,
        "docx_written": 0,
        "leads_failed": 0,
        "linkedin_profiles_scraped": 0,
        "linkedin_signed_out": 0,
    }
    failures: list[dict] = []
    total = len(leads)

    for idx, lead in enumerate(leads, 1):
        try:
            populated_lead = sf_client.populate_is_packaging(
                page=page,
                lead=lead,
                salesforce_org_url=salesforce_org_url,
            )
            if not enrich_lead.is_packaging_lead(populated_lead):
                counters["notes_skipped_non_pk"] += 1
                continue

            enrichment = _run_enrichment(
                lead=populated_lead,
                output_dir=output_dir,
                linkedin_scrape=scrape_fn,
                linkedin_scrape_min_confidence=(
                    linkedin_scrape_min_confidence
                ),
            )
            if enrichment.docx_path:
                counters["docx_written"] += 1
            if enrichment.linkedin_scrape_attempted:
                if enrichment.profile is not None:
                    counters["linkedin_profiles_scraped"] += 1
                else:
                    counters["linkedin_signed_out"] += 1

            write_result = write_note.write_note_to_lead(
                page=page,
                options=write_note.NoteWriteOptions(
                    lead=populated_lead,
                    note=enrichment.note,
                    salesforce_org_url=salesforce_org_url,
                ),
                now=datetime.now(tz=timezone.utc),
                dry_run=False,
                ledger=ledger,
            )
            if write_result.status == "written":
                counters["notes_written"] += 1
                # Issue #783 — pause to respect the ContentNote
                # throttle, but only between writes. The last Lead
                # in the batch never gets a trailing sleep (the
                # cron tick ends, nothing else hits the throttle).
                if idx < total and pause_between_notes_seconds > 0:
                    sleep_fn(pause_between_notes_seconds)
            elif write_result.status == "skipped_non_pk":
                counters["notes_skipped_non_pk"] += 1
            elif write_result.status == "skipped_recent":
                counters["notes_skipped_recent"] += 1
        except Exception as exc:  # noqa: BLE001
            counters["leads_failed"] += 1
            failures.append({"idx": idx, "lead": lead, "exc": exc})
            print(
                f"[{idx}/{total}] FAILED "
                f"{lead.record_id} {lead.name}: {exc}",
                file=sys.stderr,
            )

    return counters, failures


def _run_batch_live(
    *,
    salesforce_org_url: str,
    storage_path: Path,
    headless: bool,
    op_vault: str,
    op_item: str,
    limit: int,
    output_dir: Path,
    ledger: enrichment_ledger.EnrichmentLedger,
    linkedin_scraping_enabled: bool = False,
    linkedin_scrape_min_confidence: int = 70,
    pause_between_notes_seconds: int = 90,
) -> _BatchLiveResult:  # pragma: no cover
    """Drive `--batch --allow-live` in ONE Playwright session. Issue #776.

    Pre-fix path: `_run_batch_fetch` opened browser #1 to fetch the list,
    then the batch loop in `main()` called `_run_live_note_write` per
    lead — each opening its own browser + SSO replay. A 30-lead cycle
    paid 31 cold launches and 31 SSO replays.

    This function collapses both responsibilities into one
    `sync_playwright` lifecycle: one browser, one context, one SSO,
    then iterate every Lead through enrichment + populate_is_packaging
    + write_note_to_lead on the shared page. Per-lead failures are
    caught and recorded so a single bad Lead does not abort the batch;
    the caller renders the FAILED LEADS block.

    Does NOT catch `sf_client.ZeroLeadsFoundError` — that one
    propagates so the caller can render the operator-readable
    `LEAD LIST IS EMPTY` block (Bug 3 fix, same issue).

    `pragma: no cover` — live correctness is validated at the operator
    checkpoint. The dispatch routing is unit-tested via the
    `--batch --allow-live` → `_run_batch_live` contract test.
    """

    creds = _salesforce_credentials_provider(op_vault=op_vault, op_item=op_item)

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

                # ZeroLeadsFoundError propagates to the caller, which
                # renders the operator-readable empty-list summary.
                leads = _fetch_live_batch_candidates(
                    page=page,
                    salesforce_org_url=salesforce_org_url,
                    limit=limit,
                )
                # Echo for parity with the dry-run path.
                print(f"batch_fetch: leads={len(leads)} cap={limit}")

                # Build the LinkedIn scrape closure once. The closure
                # captures the shared Page so each enrichment in the
                # batch reuses the same browser context (no new SSO,
                # no new context launches per Lead). When the flag is
                # off, `scrape_fn` is None and `_run_enrichment` skips
                # the scrape entirely.
                scrape_fn = None
                if linkedin_scraping_enabled:
                    from scripts.research import linkedin_scraper  # noqa: PLC0415

                    def scrape_fn(*, profile_url: str):  # type: ignore[misc]
                        return linkedin_scraper.scrape_profile(
                            profile_url=profile_url, page=page
                        )

                counters, failures = _iterate_batch_writes(
                    leads=leads,
                    page=page,
                    salesforce_org_url=salesforce_org_url,
                    output_dir=output_dir,
                    ledger=ledger,
                    scrape_fn=scrape_fn,
                    linkedin_scrape_min_confidence=(
                        linkedin_scrape_min_confidence
                    ),
                    pause_between_notes_seconds=(
                        pause_between_notes_seconds
                    ),
                )

                return _BatchLiveResult(
                    leads_evaluated=len(leads),
                    counters=counters,
                    failures=failures,
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

    creds = _salesforce_credentials_provider(op_vault=op_vault, op_item=op_item)

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
    linkedin_scrape: Optional[enrich_lead.LinkedInScrapeFn] = None,
    linkedin_scrape_min_confidence: int = 70,
) -> enrich_lead.EnrichmentResult:
    """Run the research → render → docx pipeline for one Lead.

    Constructs the live `Dependencies` bundle from the published
    adapter functions and delegates to `enrich_lead.enrich`. Tests
    monkeypatch this seam to avoid network and python-docx.

    `company_hint` is `None` in Phase 2 — `LeadRow` does not yet
    carry an explicit company field, and the Perplexity / LinkedIn
    adapters degrade gracefully without it. Phase 3 will populate
    the hint from the All Sources PK Leads report column.

    Issue #781 — when `linkedin_scrape` is non-None, the LinkedIn
    profile scraper runs against the top-confidence candidate URL
    (subject to the min-confidence gate). The caller is responsible
    for capturing the shared Playwright Page into the closure so
    every Lead in the batch reuses the same browser context.
    """

    deps = enrich_lead.Dependencies(
        perplexity_research=perplexity.research_lead,
        linkedin_discover=linkedin_search.discover_candidates,
        claude_angles=claude_angles.generate,
        linkedin_scrape=linkedin_scrape,
        linkedin_scrape_min_confidence=linkedin_scrape_min_confidence,
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
    leads_failed: int
    # Issue #781 — LinkedIn scraper telemetry. Default 0 so existing
    # callers that don't yet pass the new fields stay green. Both
    # batch and single-lead paths fill them when the scraper is wired.
    linkedin_profiles_scraped: int = 0
    linkedin_signed_out: int = 0


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

    1. `populate_is_packaging` navigates to the record detail page,
       reads Business Unit -> PACKAGING, and returns a LeadRow whose
       `is_packaging` is True iff the checkbox is checked. This is the
       cross-division gate (P0 per SKILL.md) — performed before any
       write attempt so a non-PK Lead never reaches the Note form at
       all.

    2. `write_note_to_lead` runs the recency-and-write path against
       the populated lead, using the injected SerenDB ledger as the
       recency oracle.

    Marked `pragma: no cover` — live correctness is validated at the
    Phase 4 operator checkpoint. Tests monkeypatch this seam.
    """

    creds = _salesforce_credentials_provider(op_vault=op_vault, op_item=op_item)

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
        f"leads_failed={summary.leads_failed}",
        f"linkedin_profiles_scraped={summary.linkedin_profiles_scraped}",
        f"linkedin_signed_out={summary.linkedin_signed_out}",
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

    # Issue #848 — make the skill run identically regardless of launch
    # directory. (1) Load the whole `.env` into os.environ from a
    # stable location before any credential read, so the 1Password
    # path (OP_SERVICE_ACCOUNT_TOKEN / OP_VAULT / OP_ITEM) and Path-A
    # SF_* vars resolve without `set -a; . ./.env`. (2) Resolve
    # config.json from the same launch-independent search order. Both
    # run before any command branch touches config or credentials.
    from scripts import seren_client  # noqa: PLC0415

    seren_client.load_dotenv_into_environ(
        [directory / ".env" for directory in _config_search_dirs()]
    )
    args.config = _resolve_config_path(args.config)

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
        # Persist the run for `/pk-status` to read. Only on successful
        # live shares — dry-runs and skipped_no_email runs must not
        # surface a fake/empty URL to the operator on the next slash
        # command. Issue #779.
        if not args.dry_run and result.status == "shared":
            from scripts.storage import weekly_run_log  # noqa: PLC0415

            now = datetime.now(tz=timezone.utc)
            from zoneinfo import ZoneInfo  # noqa: PLC0415

            local_iso = now.astimezone(ZoneInfo("America/New_York")).isocalendar()
            week_label = f"{local_iso.year}-W{local_iso.week:02d}"
            weekly_run_log.append(
                args.state_dir,
                {
                    "week_label": week_label,
                    "title": f"PK Weekly Status — {week_label}",
                    "doc_url": result.doc_url,
                    "shared_with": result.shared_with,
                    "status": result.status,
                    "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        return 0

    # args.command == "run"
    # Hoist ledger setup so it's resolved once per cycle regardless of
    # batch / single-lead — the per-lead live-write inside the batch
    # loop reuses the same ledger handle. SerenDB URI gate fails closed
    # before any browser launches.
    ledger = None
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

    # Issue #781 — LinkedIn profile scraper opt-in. Default false; the
    # operator turns it on in `config.json` per Lead enrichment cycle.
    # Read once at the top of the run so every batch / single-lead
    # dispatch path sees the same setting.
    inputs = config.get("inputs") or {}
    linkedin_scraping_enabled = bool(inputs.get("linkedin_scraping_enabled", False))
    linkedin_scrape_min_confidence = int(
        inputs.get("linkedin_scrape_min_confidence", 70)
    )

    # Issue #783 — ContentNote throttle. CLI flag wins; config wins
    # over the default. Default is 90s — Salesforce's observed
    # minimum window between sequential Note writes on Lightning.
    if args.pause_between_notes is not None:
        pause_between_notes_seconds = args.pause_between_notes
    else:
        pause_between_notes_seconds = int(
            inputs.get("pause_between_notes_seconds", 90)
        )

    if args.batch:
        # Bug 3 (issue #776): catch ZeroLeadsFoundError at the dispatch
        # boundary so the FLS-gap / empty-list case prints the
        # cron-parseable summary instead of a Python traceback. Bug 4
        # (same issue): --batch --allow-live dispatches to
        # _run_batch_live which holds one Playwright session for the
        # whole cycle instead of N+1.
        try:
            if args.allow_live:
                live_result = _run_batch_live(
                    salesforce_org_url=salesforce_org_url,
                    storage_path=args.storage_path,
                    headless=args.headless,
                    op_vault=op_vault,
                    op_item=op_item,
                    limit=args.max_leads,
                    output_dir=args.output_dir,
                    ledger=ledger,
                    linkedin_scraping_enabled=linkedin_scraping_enabled,
                    linkedin_scrape_min_confidence=(
                        linkedin_scrape_min_confidence
                    ),
                    pause_between_notes_seconds=(
                        pause_between_notes_seconds
                    ),
                )
                leads_evaluated = live_result.leads_evaluated
                counters = live_result.counters
                failures = live_result.failures
            else:
                leads = _run_batch_fetch(
                    salesforce_org_url=salesforce_org_url,
                    storage_path=args.storage_path,
                    headless=args.headless,
                    op_vault=op_vault,
                    op_item=op_item,
                    limit=args.max_leads,
                )
                print(f"batch_fetch: leads={len(leads)} cap={args.max_leads}")

                counters = {
                    "notes_written": 0,
                    "notes_skipped_non_pk": 0,
                    "notes_skipped_recent": 0,
                    "docx_written": 0,
                    "leads_failed": 0,
                }
                # Collect per-lead failures for the FAILED LEADS block
                # on stdout (issue #774). stderr still gets the per-line
                # print for log-collection symmetry — it's free and
                # machine-readable.
                failures = []

                for idx, lead in enumerate(leads, 1):
                    try:
                        enrichment = _run_enrichment(
                            lead=lead, output_dir=args.output_dir
                        )
                        if enrichment.docx_path:
                            counters["docx_written"] += 1
                    except Exception as exc:  # noqa: BLE001
                        counters["leads_failed"] += 1
                        failures.append(
                            {"idx": idx, "lead": lead, "exc": exc}
                        )
                        print(
                            f"[{idx}/{len(leads)}] FAILED "
                            f"{lead.record_id} {lead.name}: {exc}",
                            file=sys.stderr,
                        )

                leads_evaluated = len(leads)
        except sf_client.ZeroLeadsFoundError as exc:
            print()
            print("LEAD LIST IS EMPTY:")
            print(f"  {exc}")
            print()
            _print_run_summary(
                RunSummary(
                    command="run",
                    dry_run=args.dry_run,
                    leads_evaluated=0,
                    notes_written=0,
                    notes_skipped_non_pk=0,
                    notes_skipped_recent=0,
                    docx_written=0,
                    leads_failed=0,
                )
            )
            return 0

        # Common FAILED LEADS block + summary for both batch paths.
        if failures:
            print()
            print(f"FAILED LEADS ({len(failures)}):")
            for failure in failures:
                lead_obj = failure["lead"]
                exc = failure["exc"]
                print(
                    f"  [{failure['idx']}/{leads_evaluated}] "
                    f"{lead_obj.record_id} — {lead_obj.name}"
                )
                print(f"          {type(exc).__name__}: {exc}")
            print()

        _print_run_summary(
            RunSummary(
                command="run",
                dry_run=args.dry_run,
                leads_evaluated=leads_evaluated,
                **counters,
            )
        )
        return 0

    try:
        lead = _run_dry_run(
            salesforce_org_url=salesforce_org_url,
            storage_path=args.storage_path,
            headless=args.headless,
            op_vault=op_vault,
            op_item=op_item,
            candidate_limit=args.max_leads,
        )
    except sf_client.ZeroLeadsFoundError as exc:
        print()
        print("NO PACKAGING LEAD FOUND:")
        print(f"  {exc}")
        print()
        _print_run_summary(
            RunSummary(
                command="run",
                dry_run=args.dry_run,
                leads_evaluated=0,
                notes_written=0,
                notes_skipped_non_pk=0,
                notes_skipped_recent=0,
                docx_written=0,
                leads_failed=0,
            )
        )
        return 0

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
    if enrichment.angles.angles:
        first_angle = enrichment.angles.angles[0]
        print(f"  angle:     {first_angle[:120]}")
    else:
        print("  angle:     (no angles generated)")

    note_status = "not_attempted"
    if args.allow_live:
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
            leads_failed=0,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
