from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_CONTRACT = REPO_ROOT / "kalshi" / "_shared" / "output-contract.md"
SUMMARY_TEMPLATE = REPO_ROOT / "kalshi" / "_shared" / "desktop-summary-template.md"
KALSHI_SKILLS = [
    REPO_ROOT / "kalshi" / "hybrid-signal-trader" / "SKILL.md",
    REPO_ROOT / "kalshi" / "watchlist-explainer" / "SKILL.md",
    REPO_ROOT / "kalshi" / "consensus-divergence-monitor" / "SKILL.md",
    REPO_ROOT / "kalshi" / "macro-signal-monitor" / "SKILL.md",
]


def test_shared_contract_docs_exist() -> None:
    assert SHARED_CONTRACT.exists()
    assert SUMMARY_TEMPLATE.exists()


def test_every_kalshi_skill_references_shared_contract_and_summary_shape() -> None:
    required_terms = (
        "kalshi/_shared/output-contract.md",
        "kalshi/_shared/desktop-summary-template.md",
    )

    for skill_md in KALSHI_SKILLS:
        content = skill_md.read_text(encoding="utf-8")
        for term in required_terms:
            assert term in content, f"{skill_md} missing shared contract reference {term}"


def test_shared_contract_lists_required_payload_fields() -> None:
    content = SHARED_CONTRACT.read_text(encoding="utf-8")

    for field_name in (
        "run_status",
        "mode",
        "generated_at",
        "signal_health",
        "market_candidates",
        "selected_trades",
        "watchlist",
        "blocked_reasons",
        "rationale",
        "risk_note",
        "freshness",
        "desktop_summary",
        "audit",
    ):
        assert field_name in content
