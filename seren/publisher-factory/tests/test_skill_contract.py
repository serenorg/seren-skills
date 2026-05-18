from __future__ import annotations

from pathlib import Path
import re


SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_skill_identity_and_live_catalog_guard() -> None:
    content = _skill_text()
    lower = content.lower()

    assert "name: publisher-factory" in content
    assert "list_agent_publishers" in content
    assert "get_agent_publisher" in content
    assert "list_organizations" in content
    assert "organization_id" in content
    assert re.search(r"no arguments|without arguments|empty argument", lower)
    assert "fuzzy" in lower
    assert re.search(r"exact\s+existence check", lower)
    assert "third-party" in lower
    assert 'list_agent_publishers` with `slug: "asana"' not in content


def test_research_scope_and_verification_gates() -> None:
    lower = _skill_text().lower()

    for phrase in [
        "top 10 competitors",
        "20 companies total",
        "perplexity",
        "public api docs",
        "skip",
    ]:
        assert phrase in lower


def test_publisher_contract_and_report_groups() -> None:
    lower = _skill_text().lower()

    for phrase in [
        "integration_type: api",
        "publisher_category: integration",
        "x402_per_request",
        "undocumented_endpoint_policy: default_deny",
        "clone the live asana",
        "oauth",
        "never reuse asana's oauth provider",
        "api key",
        "passthrough headers",
        "protected",
        "logo_status: missing",
        "do not persist",
    ]:
        assert phrase in lower

    for group in ["deployed", "existing", "updated", "skipped", "blocked"]:
        assert re.search(rf"`?{group}`?", lower), f"missing report group: {group}"
