from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TARGET_FILES = [
    "alpaca/saas-short-trader/scripts/strategy_engine.py",
    "alpaca/sass-short-trader-delta-neutral/scripts/strategy_engine.py",
    "alphagrowth/euler-base-vault-bot/scripts/agent.py",
    "coinbase/grid-trader/scripts/agent.py",
    "coinbase/smart-dca-bot/scripts/agent.py",
    "curve/curve-gauge-yield-trader/scripts/agent.py",
    "kraken/carf-dac8-crypto-asset-reporting/scripts/agent.py",
    "kraken/grid-trader/scripts/agent.py",
    "kraken/money-mode-router/scripts/agent.py",
    "kraken/smart-dca-bot/scripts/agent.py",
    "ledger/ledger-signing/scripts/agent.py",
    "polymarket/bot/scripts/agent.py",
    "polymarket/high-throughput-paired-basis-maker/scripts/agent.py",
    "polymarket/liquidity-paired-basis-maker/scripts/agent.py",
    "polymarket/maker-rebate-bot/scripts/agent.py",
    "polymarket/paired-market-basis-maker/scripts/agent.py",
    "prophet/prophet-adversarial-auditor/scripts/agent.py",
    "prophet/prophet-growth-agent/scripts/agent.py",
    "prophet/prophet-market-seeder/scripts/agent.py",
    "seren/customer-support-intake/scripts/agent.py",
    "seren/seren-scheduler/scripts/agent.py",
    "spectra/spectra-pt-yield-trader/scripts/agent.py",
    "wellsfargo/bank-statement-processing/scripts/run.py",
    "wellsfargo/budget-tracker/scripts/agent.py",
    "wellsfargo/cash-flow-statement/scripts/run.py",
    "wellsfargo/income-statement/scripts/run.py",
    "wellsfargo/net-worth-tracker/scripts/agent.py",
    "wellsfargo/recurring-transactions/scripts/run.py",
    "wellsfargo/tax-prep/scripts/agent.py",
    "wellsfargo/vendor-analysis/scripts/agent.py",
    "zkp2p/peer-to-peer-payments-exchange/scripts/agent.py",
]


def _extract_bootstrap_helper(relative_path: str):
    source_path = REPO_ROOT / relative_path
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(source_path))

    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_bootstrap_config_path":
            helper_source = ast.get_source_segment(source, node)
            assert helper_source is not None, f"Could not extract helper from {relative_path}"
            namespace = {"Path": Path}
            exec(helper_source, namespace)  # noqa: S102
            return namespace["_bootstrap_config_path"]

    raise AssertionError(f"{relative_path} is missing _bootstrap_config_path")


@pytest.mark.parametrize("relative_path", TARGET_FILES, ids=TARGET_FILES)
def test_bootstrap_helper_copies_config_example(relative_path: str, tmp_path: Path) -> None:
    helper = _extract_bootstrap_helper(relative_path)
    config_path = tmp_path / "config.json"
    example_path = tmp_path / "config.example.json"
    example_body = '{\n  "dry_run": true\n}\n'
    example_path.write_text(example_body, encoding="utf-8")

    resolved = helper(str(config_path))

    assert resolved == config_path
    assert config_path.read_text(encoding="utf-8") == example_body


@pytest.mark.parametrize("relative_path", TARGET_FILES, ids=TARGET_FILES)
def test_bootstrap_helper_leaves_missing_config_missing_without_example(
    relative_path: str,
    tmp_path: Path,
) -> None:
    helper = _extract_bootstrap_helper(relative_path)
    config_path = tmp_path / "config.json"

    resolved = helper(str(config_path))

    assert resolved == config_path
    assert not config_path.exists()


@pytest.mark.parametrize("relative_path", TARGET_FILES, ids=TARGET_FILES)
def test_runtime_routes_config_loading_through_bootstrap(relative_path: str) -> None:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    assert "config.example.json" in source, f"{relative_path} does not reference config.example.json"
    assert source.count("_bootstrap_config_path(") >= 2, (
        f"{relative_path} defines the bootstrap helper but does not route config loading through it"
    )
