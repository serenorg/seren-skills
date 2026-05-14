"""Top-level entrypoint for pk-lead-intelligence runs.

Phase 2 supports exactly one command:

    python scripts/agent.py --command run --dry-run

That path:

1. Reads SF credentials from 1Password via `auth.op_service_account`.
2. Launches a Playwright Chromium with the persisted storage_state
   (if any) and drives Microsoft SSO via `auth.microsoft_sso`.
3. Navigates to the Lead list and reads the first Lead row.
4. Runs the enrichment pipeline (`sf.enrich_lead`) over that lead —
   Perplexity research, LinkedIn discovery, Claude hypothesis — and
   writes a local `.docx` Note for operator review.

There is no Salesforce write in Phase 2 — those land in Phase 4 once
the operator has reviewed at least five dry-run Notes. Live writes
are gated by `--allow-live`, which Phase 2 cannot honor; the flag is
parsed and rejected so the operator does not develop a false sense
of which gates exist.
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

from scripts.auth import microsoft_sso  # noqa: E402
from scripts.auth import op_service_account  # noqa: E402
from scripts.research import claude_hypothesis  # noqa: E402
from scripts.research import linkedin_search  # noqa: E402
from scripts.research import perplexity  # noqa: E402
from scripts.sf import client as sf_client  # noqa: E402
from scripts.sf import enrich_lead  # noqa: E402


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
        choices=["run"],
        help="Top-level command. Phase 2 supports `run`.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Required in Phase 2. Disables every Salesforce write "
            "path (which do not exist yet anyway)."
        ),
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help=(
            "Reserved for Phase 4+. Live writes also require "
            "config.json `inputs.live_mode=true` — both must be set."
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns a process exit code."""

    args = _build_parser().parse_args(argv)

    if args.command == "run" and not args.dry_run:
        # No live write paths exist yet; refuse to pretend otherwise.
        print(
            "Phase 2 supports --dry-run only. Re-run with --dry-run.",
            file=sys.stderr,
        )
        return 2

    if args.allow_live:
        print(
            "--allow-live is reserved for Phase 4+ and is rejected in "
            "Phase 2. Drop the flag and re-run with --dry-run.",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
