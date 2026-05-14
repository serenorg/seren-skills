from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from bootstrap import GatewayError, bootstrap_affiliate_context  # noqa: E402
from common import DEFAULT_CONFIG, tracked_link  # noqa: E402
from digest import build_daily_digest  # noqa: E402
from drafting import build_draft_batches  # noqa: E402


class StubAffiliateGateway:
    def __init__(self, *, missing_profile: bool = False, partner_links: dict | None = None) -> None:
        self.missing_profile = missing_profile
        self.partner_links = partner_links or {
            "partner_links": [
                {
                    "program_id": "seren-bucks-default",
                    "program_slug": "seren-bucks",
                    "program_name": "SerenBucks Affiliate Program",
                    "partner_link_url": "https://serendb.com?ref=SRN_TEST123",
                }
            ]
        }
        self.calls: list[tuple[str, str, str]] = []

    def call(self, publisher: str, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append((publisher, method, path))
        if (publisher, method, path) == ("seren-affiliates", "GET", "/affiliates/me"):
            if self.missing_profile:
                raise GatewayError("profile missing", status_code=404)
            return {"id": "affiliate-1", "referral_code": "SRN_TEST123"}
        if (publisher, method, path) == ("seren-affiliates", "POST", "/affiliates"):
            return {"id": "affiliate-1", "referral_code": "SRN_TEST123"}
        if (
            publisher,
            method,
            path,
        ) == ("seren-affiliates", "GET", "/affiliates/me/partner-links"):
            return self.partner_links
        raise AssertionError(f"unexpected gateway call: {(publisher, method, path)}")


def test_bootstrap_registers_fetches_live_srn_link_and_updates_runtime_config() -> None:
    config = deepcopy(DEFAULT_CONFIG)
    config["dry_run"] = False
    gateway = StubAffiliateGateway(missing_profile=True)

    result = bootstrap_affiliate_context(config, gateway=gateway)

    assert result["status"] == "ok"
    assert result["registered_this_run"] is True
    assert result["program"]["program_id"] == "seren-bucks-default"
    assert result["program"]["tracked_link"] == "https://serendb.com?ref=SRN_TEST123"
    assert result["program_state"]["tracked_link"] == "https://serendb.com?ref=SRN_TEST123"
    assert tracked_link(config) == "https://serendb.com?ref=SRN_TEST123"
    assert gateway.calls == [
        ("seren-affiliates", "GET", "/affiliates/me"),
        ("seren-affiliates", "POST", "/affiliates"),
        ("seren-affiliates", "GET", "/affiliates/me/partner-links"),
    ]


def test_bootstrap_fails_closed_when_partner_link_is_not_a_real_srn_link() -> None:
    config = deepcopy(DEFAULT_CONFIG)
    config["dry_run"] = False
    gateway = StubAffiliateGateway(
        partner_links={
            "partner_links": [
                {
                    "program_id": "seren-bucks-default",
                    "program_slug": "seren-bucks",
                    "program_name": "SerenBucks Affiliate Program",
                    "partner_link_url": "https://serendb.com?ref=default",
                }
            ]
        }
    )

    result = bootstrap_affiliate_context(config, gateway=gateway)

    assert result["status"] == "error"
    assert result["error_code"] == "affiliate_tracked_link_invalid"
    assert result["fail_closed"] is True


def test_drafts_and_digest_use_exact_bootstrapped_link() -> None:
    config = deepcopy(DEFAULT_CONFIG)
    config["dry_run"] = False
    live_link = "https://serendb.com?ref=SRN_TEST123"
    affiliate = bootstrap_affiliate_context(config, gateway=StubAffiliateGateway())
    proposal = {
        "top10": [
            {
                "candidate_id": "cand-1",
                "full_name": "Alex Chen",
                "organization": "Personal",
                "rank_position": 1,
            }
        ]
    }

    drafts = build_draft_batches(proposal, config)
    digest = build_daily_digest(
        config=config,
        auth_db={"auth_path": "seren_api_key"},
        affiliate=affiliate,
        sync_result={
            "discovered_count": 1,
            "qualified_count": 1,
            "quota_shortfall": True,
            "source_counts": {"gmail_sent": 1},
        },
        proposal=proposal,
        drafts=drafts,
        send_plan={
            "new_outbound_batch": {"count": 1},
            "reply_batch": {"count": 0},
        },
        reconciliation={"dnc_events": []},
    )

    assert drafts["new_outbound"][0]["tracked_link"] == live_link
    assert live_link in drafts["new_outbound"][0]["message_body"]
    assert f"- Tracked link: {live_link}" in digest["markdown"]
