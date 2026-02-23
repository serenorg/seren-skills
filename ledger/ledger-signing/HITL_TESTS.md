# Ledger Signing HITL Tests

## Preconditions

- Ledger device connected and unlocked.
- Ethereum app open on Ledger.
- Dependencies installed: `pip install -r requirements.txt`

## Test 1: Message signing

1. Copy config:
   - `cp config.example.json config.live-message.json`
2. Edit config:
   - `"dry_run": false`
   - `"inputs.payload_kind": "message"`
   - `"inputs.derivation_path": "44'/60'/0'/0/0"`
   - `"inputs.payload_hex": "<utf8 message hex>"`
3. Run:
   - `python scripts/agent.py --config config.live-message.json --execute`
4. Expect:
   - Ledger prompts to review/sign message.
   - Output contains `status=signed` and `signature_hex`.

## Test 2: Transaction signing

1. Copy config:
   - `cp config.example.json config.live-transaction.json`
2. Edit config:
   - `"dry_run": false`
   - `"inputs.payload_kind": "transaction"`
   - `"inputs.derivation_path": "44'/60'/0'/0/0"`
   - `"inputs.payload_hex": "<unsigned legacy tx RLP hex>"`
3. Run:
   - `python scripts/agent.py --config config.live-transaction.json --execute`
4. Expect:
   - Ledger prompts to review/sign transaction fields.
   - Output contains `status=signed` and `signature_hex`.

## Test 3: EIP-712 typed data signing (hashed mode)

1. Copy config:
   - `cp config.example.json config.live-typed-data.json`
2. Edit config:
   - `"dry_run": false`
   - `"inputs.payload_kind": "typed_data"`
   - `"inputs.derivation_path": "44'/60'/0'/0/0"`
   - `"inputs.domain_separator_hex": "<32-byte domain separator hex>"`
   - `"inputs.hash_struct_message_hex": "<32-byte hashStruct(message) hex>"`
   - leave `"inputs.payload_hex": ""` (or set 64-byte combined fallback)
3. Run:
   - `python scripts/agent.py --config config.live-typed-data.json --execute`
4. Expect:
   - Ledger prompts to review/sign typed data hash payload.
   - Output contains `status=signed` and `signature_hex`.

## Safety Gate Check

Run with default config:

- `python scripts/agent.py --config config.example.json --execute`

Expected:

- Runtime rejects execution while `dry_run=true`.
