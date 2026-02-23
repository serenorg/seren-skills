# Ledger Signing

USB/HID runtime skill for signing transactions and messages on a Ledger device.

## Location

- Skill doc: `ledger/ledger-signing/SKILL.md`
- Runtime: `ledger/ledger-signing/scripts/agent.py`
- Human-in-the-loop tests: `ledger/ledger-signing/HITL_TESTS.md`

## Supported Payloads

- `transaction`
- `message`
- `typed_data` (EIP-712)

## Quick Start

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create config:

```bash
cp config.example.json config.json
```

3. Set runtime inputs in `config.json`:

- `dry_run`: `false`
- `inputs.payload_kind`
- `inputs.derivation_path`
- `inputs.payload_hex` for `transaction` / `message`
- EIP-712 `typed_data`:
  - preferred: `inputs.domain_separator_hex` + `inputs.hash_struct_message_hex` (32-byte each)
  - compatible fallback: `inputs.payload_hex` as 64-byte combined blob
    `[domainSeparator(32) || hashStruct(message)(32)]`

4. Run execute mode:

```bash
python scripts/agent.py --config config.json --execute
```

## Safety

- `dry_run=true` cannot sign.
- `--execute` is required for live device signing.
- The runtime uses direct Ledger USB/HID transport through `ledgerblue`.
- For session-based automation, the owner may extend the Ledger auto-lock timeout (if supported), then unlock once and keep the session open while the agent signs.
- Warning: extended unlocked sessions weaken physical security. Anyone with physical access to the unlocked device may be able to approve/sign transactions.
