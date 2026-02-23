---
name: ledger-signing
description: "Guide Ledger device owners through secure transaction and message signing with explicit support for clear signing and blind signing fallback flows."
---

# Ledger Signing

Direct USB/HID runtime execution for Ledger signing flows.

## When to Use

- ledger signing
- clear sign transaction
- blind sign transaction
- sign message with ledger

## Workflow Summary

1. `validate_request` uses `transform.validate_signing_request`
2. `render_clear_signing_flow` uses `transform.render_ledger_clear_signing_guide`
3. `render_blind_signing_flow` uses `transform.render_ledger_blind_signing_guide`
4. `render_final_response` uses `transform.render_dual_signing_response`

## Runtime

- Entry point: `scripts/agent.py`
- Transport: direct USB/HID via `ledgerblue`
- Supported payload kinds:
  - `transaction`
  - `message`
- Not yet implemented:
  - `typed_data` (EIP-712)

## Quick Start

1. Install dependencies:
   - `pip install -r requirements.txt`
2. Copy config:
   - `cp config.example.json config.json`
3. Set:
   - `dry_run=false`
   - `inputs.payload_kind`
   - `inputs.derivation_path`
   - `inputs.payload_hex`
4. Run live signing:
   - `python scripts/agent.py --config config.json --execute`

## HITL Validation

- See `HITL_TESTS.md` for merge-gate test cases and evidence checklist.
