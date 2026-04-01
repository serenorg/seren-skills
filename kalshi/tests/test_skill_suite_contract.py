from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KALSHI_SKILLS = {
    "hybrid-signal-trader": REPO_ROOT / "kalshi" / "hybrid-signal-trader",
    "watchlist-explainer": REPO_ROOT / "kalshi" / "watchlist-explainer",
    "consensus-divergence-monitor": REPO_ROOT / "kalshi" / "consensus-divergence-monitor",
    "macro-signal-monitor": REPO_ROOT / "kalshi" / "macro-signal-monitor",
}

REQUIRED_FIELDS = (
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
)

REQUIRED_SUMMARY_TERMS = (
    "mini research note",
    "what the contract is",
    "what `gap` saw",
    "what `coil` saw",
    "watchlist-only",
    "signal-health caveat",
)


def test_shared_folder_is_removed() -> None:
    assert not (REPO_ROOT / "kalshi" / "_shared").exists()


def test_every_kalshi_skill_has_local_contract_docs() -> None:
    for skill_name, skill_dir in KALSHI_SKILLS.items():
        assert (skill_dir / "references" / "output-contract.md").exists(), skill_name
        assert (skill_dir / "references" / "desktop-summary-template.md").exists(), skill_name


def test_every_kalshi_skill_references_local_contract_docs() -> None:
    for skill_name, skill_dir in KALSHI_SKILLS.items():
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "references/output-contract.md" in content, skill_name
        assert "references/desktop-summary-template.md" in content, skill_name
        assert "kalshi/_shared/" not in content, skill_name


def test_local_contract_docs_list_required_payload_fields() -> None:
    for skill_name, skill_dir in KALSHI_SKILLS.items():
        content = (skill_dir / "references" / "output-contract.md").read_text(
            encoding="utf-8"
        )
        for field_name in REQUIRED_FIELDS:
            assert field_name in content, f"{skill_name} missing field {field_name}"


def test_local_summary_templates_capture_required_note_shape() -> None:
    for skill_name, skill_dir in KALSHI_SKILLS.items():
        content = (skill_dir / "references" / "desktop-summary-template.md").read_text(
            encoding="utf-8"
        )
        for term in REQUIRED_SUMMARY_TERMS:
            assert term in content, f"{skill_name} missing term {term}"
