"""Critical-only tests for arbitrage.intelligence.

Coverage:
  - test_disabled_returns_no_warnings_no_calls: locks the gating
    contract — when intelligence is disabled in config, the publisher
    must not be called (cost discipline).
  - test_low_balance_publisher_does_not_block_cycle: 402 must downgrade
    to a warning, not crash. The arb-bot should still trade on raw
    spread when the intelligence layer is unavailable.
  - test_high_volatility_returns_warning: a noisy polymarket reference
    must produce a health warning so scoring downgrades the pair.
"""

from __future__ import annotations

from arbitrage.intelligence import IntelligenceConfig, assess_pair_health


class _RaisingGateway:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = []

    def call(self, *a, **kw):  # noqa: ANN001
        self.calls.append((a, kw))
        raise self.exc


class _UnreachableGateway:
    def call(self, *a, **kw):  # noqa: ANN001
        raise AssertionError("intelligence must not call when disabled")


def test_disabled_returns_no_warnings_no_calls() -> None:
    config = IntelligenceConfig(enabled=False)
    verdict = assess_pair_health(
        gateway=_UnreachableGateway(),
        polymarket_condition_id="abc",
        config=config,
    )
    assert verdict.health_warnings == []
    assert verdict.enriched is False


def test_low_balance_publisher_does_not_block_cycle() -> None:
    gateway = _RaisingGateway(RuntimeError("HTTP 402: insufficient balance"))
    verdict = assess_pair_health(
        gateway=gateway,
        polymarket_condition_id="abc",
        config=IntelligenceConfig(enabled=True),
    )
    assert any("low_balance" in w for w in verdict.health_warnings)
    assert verdict.enriched is False


def test_high_volatility_returns_warning(stub_gateway) -> None:
    stub_gateway.register(
        "seren-polymarket-intelligence",
        "GET",
        "/api/polymarket/correlations?condition_id=abc",
        {"basis_sigma": 0.12},
    )
    config = IntelligenceConfig(enabled=True, max_basis_volatility=0.05)
    verdict = assess_pair_health(
        gateway=stub_gateway,
        polymarket_condition_id="abc",
        config=config,
    )
    assert any("polymarket_volatility_high" in w for w in verdict.health_warnings)
    assert verdict.enriched is True
