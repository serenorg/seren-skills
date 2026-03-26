"""Tests for structured trade summary block output (issue #296).

Verifies that scan output includes a contiguous, grep-able summary block
so any LLM agent can extract complete trade data without partial reads.
"""

import sys
from io import StringIO
from pathlib import Path

import pytest

BOT_SCRIPTS = str(Path(__file__).resolve().parent.parent / "bot" / "scripts")


@pytest.fixture(autouse=True)
def _add_bot_scripts_to_path():
    if BOT_SCRIPTS not in sys.path:
        sys.path.insert(0, BOT_SCRIPTS)
    yield


def _make_opportunities(n=3):
    """Build n fake opportunity dicts matching agent.py's evaluate_opportunity return."""
    opps = []
    for i in range(n):
        opps.append({
            'market': {
                'question': f'Test Market {i + 1}?',
                'market_id': f'0x{i:040x}',
                'price': 0.50 + i * 0.10,
            },
            'fair_value': 0.30 + i * 0.05,
            'confidence': 'medium',
            'edge': 0.15 + i * 0.02,
            'side': 'SELL' if i % 2 == 0 else 'BUY',
            'position_size': 5.00 + i,
            'expected_value': 2.50 + i * 0.5,
        })
    return opps


def test_summary_block_has_start_and_end_markers():
    from agent import print_trade_summary

    buf = StringIO()
    print_trade_summary(_make_opportunities(2), capital_deployed=11.0, file=buf)
    output = buf.getvalue()

    assert "=== DRY-RUN TRADE SUMMARY ===" in output
    assert "=== END TRADE SUMMARY ===" in output


def test_summary_block_contains_all_trades():
    from agent import print_trade_summary

    opps = _make_opportunities(4)
    buf = StringIO()
    print_trade_summary(opps, capital_deployed=26.0, file=buf)
    output = buf.getvalue()

    for opp in opps:
        assert opp['market']['question'] in output


def test_summary_block_contains_all_fields_per_trade():
    from agent import print_trade_summary

    opps = _make_opportunities(1)
    buf = StringIO()
    print_trade_summary(opps, capital_deployed=5.0, file=buf)
    output = buf.getvalue()

    # Every row must have side, price, FV, edge, size, EV
    assert "SELL" in output
    assert "$5.00" in output
    assert "+$2.50" in output


def test_summary_block_includes_totals():
    from agent import print_trade_summary

    opps = _make_opportunities(2)
    total_ev = sum(o['expected_value'] for o in opps)
    buf = StringIO()
    print_trade_summary(opps, capital_deployed=11.0, file=buf)
    output = buf.getvalue()

    assert "TOTAL_DEPLOYED" in output
    assert "TOTAL_EV" in output
    expected_ev = f"{'+' if total_ev >= 0 else '-'}${abs(total_ev):.2f}"
    assert expected_ev in output


def test_no_output_when_zero_opportunities():
    from agent import print_trade_summary

    buf = StringIO()
    print_trade_summary([], capital_deployed=0.0, file=buf)
    output = buf.getvalue()

    assert output == ""
