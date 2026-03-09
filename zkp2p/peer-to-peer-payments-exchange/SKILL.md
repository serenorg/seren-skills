---
name: peer-to-peer-payments-exchange
description: "Route and execute peer-to-peer fiat-to-crypto payment and exchange flows on Peer (ZKP2P), including onramp, offramp, checkout, transfer, LP, vault, analytics, explorer, and activity monitoring paths."
---

# Peer To Peer Payments Exchange

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- exchange fiat and usdc peer to peer
- route p2p payment flow on peer protocol
- orchestrate zkp2p checkout onramp offramp transfer

## Workflow Summary

1. `normalize_request` uses `transform.normalize_intent`
2. `route_intent` uses `transform.route_intent`
3. `get_market_quote` uses `connector.peer_market.get`
4. `get_protocol_context` uses `connector.peer_analytics.get`
5. `lookup_entity_context` uses `connector.peer_explorer.get`
6. `build_execution_plan` uses `connector.model.post`
7. `monitor_activity` uses `connector.peer_activity.get`
8. `summary` uses `transform.render_guide`

## Upstream Source

- Repository: https://github.com/zkp2p/zkp2p-skills/

## Referenced ZKP2P Skills

### Action Skills

- `accept-fiat-payments` -> receive fiat and settle USDC (`peer-checkout`)
- `analyze-peer-protocol` -> protocol performance and health (`peer-analytics`)
- `check-fx-rates` -> live rates/spreads/liquidity (`peer-market`)
- `earn-as-defi-manager` -> vault management and strategy (`peer-vault`, `peer-rate-optimizer`)
- `earn-on-idle-usdc` -> LP deposit and yield path (`peer-lp`)
- `fiat-to-crypto` -> onramp fiat to USDC (`peer-onramp`)
- `look-up-peer-data` -> entity/deposit/intent lookup (`peer-explorer`)
- `monitor-peer-activity` -> live protocol event monitoring (`peer-activity`)
- `pay-humans-fiat` -> offramp USDC to fiat payout (`peer-offramp`)
- `send-usdc` -> direct USDC transfer (`peer-transfer`)

### Implementation Skills

- `peer-activity`
- `peer-analytics`
- `peer-checkout`
- `peer-explorer`
- `peer-lp`
- `peer-market`
- `peer-offramp`
- `peer-onramp`
- `peer-rate-optimizer`
- `peer-transfer`
- `peer-vault`
