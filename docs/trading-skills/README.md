# Trading Skill Safety CI

PRs that change trading skills now run `scripts/validate_trading_skill_safety.py` against the changed skill directories only.

This is intentionally scoped so:

- non-trading skills are not blocked
- untouched legacy trading skills do not break unrelated PRs
- contributors get precise remediation messages when a changed trading skill is missing a required safety contract

## Who This Applies To

The validator treats a skill as trading when the skill describes or implements live or paper execution for:

- broker or exchange trading
- prediction-market execution
- DeFi swaps, vaults, staking, lending, or LP actions
- market making, quoting, arbitrage, hedging, rebalancing, or DCA

If the automatic classification is wrong, use `.github/trading-skill-safety-waivers.json` to add a reviewable classification override.

## Required `SKILL.md` Guardrails

Changed trading skills must document these operator contracts in `SKILL.md`:

- `Trade Execution Contract`
- `Pre-Trade Checklist`
- dependency validation and fail-closed remediation
- live-safety opt-in with the exact config and CLI approval required
- emergency or operator exit path when the skill can hold inventory
- `CLOB Exit Rules` or equivalent order-book execution rules when the skill uses CLOB or order-book execution

## Required Runtime Guardrails

Changed trading skills with executable runtime files must show evidence of:

- explicit live confirmation flags such as `--yes-live` or `--allow-live`
- default-safe mode such as `dry_run`, `paper`, `paper-sim`, `backtest`, `quote`, or `monitor`
- fail-closed credential, dependency, publisher, RPC, or reachability checks
- emergency exit or stop support for skills that can hold inventory
- scheduler paths that preserve the same live confirmation semantics
- marketability-aware exits for order-book-based live execution

## Focused Test Coverage

When a PR changes runtime code for a trading skill, the validator also expects focused regression coverage for:

- live confirmation gating
- dependency failure behavior
- emergency exit behavior when the skill holds inventory
- marketability-aware exits when the skill relies on order-book execution

The intent is not to force duplicated tests across copy-equivalent skills. Shared suites are acceptable if they clearly reference the affected skill.

## Waivers

Use `.github/trading-skill-safety-waivers.json` for narrow, reviewable exceptions.

Supported entries:

- `classification_overrides`
- `waivers`

Example:

```json
{
  "classification_overrides": {
    "kraken/money-mode-router": {
      "trading": false,
      "reason": "Routes users only; it does not execute orders or positions."
    }
  },
  "waivers": {
    "example/skill": {
      "rules": ["runtime_scheduler_safety"],
      "reason": "Scheduler integration is not implemented yet and tracked in a follow-up issue."
    }
  }
}
```

Use waivers sparingly. They should be temporary, explicit, and specific to the missing rule.

## Local Usage

Validate every trading skill in the repo:

```bash
python3 scripts/validate_trading_skill_safety.py --all
```

Validate only the trading skills changed in your branch:

```bash
python3 scripts/validate_trading_skill_safety.py --base-ref origin/main
```

Validate a specific skill:

```bash
python3 scripts/validate_trading_skill_safety.py --skill polymarket/maker-rebate-bot
```

## Best Practices

Use this checklist when authoring or updating a trading skill:

1. Make live execution opt-in twice: config plus explicit operator confirmation.
2. Default to a safe non-live mode.
3. Fail closed on missing libraries, credentials, publishers, RPC endpoints, or market reachability.
4. Document exactly how immediate exits are handled.
5. Provide an operator exit path when the runtime can hold positions.
6. For order-book execution, use current `tick_size`, marketable exits, and full visible-depth estimates.
7. Add focused tests for live confirmations, dependency failures, and emergency exits.
