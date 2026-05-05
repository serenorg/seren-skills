---
name: key-recovery-diagnosis
description: "Diagnose crypto wallet access-loss scenarios, classify self-recovery feasibility, guide safe local recovery attempts for simple cases, and prepare sponsor handoff summaries for expert-only cases."
---
# Crypto Key Recovery

## When to Use

Use this skill when a user says things like:

- "I lost access to my crypto wallet"
- "I forgot my wallet password"
- "I'm missing seed words"
- "I think my Ledger or Trezor is locked"
- "Can I recover this wallet or is it gone?"

Do not use this skill for:

- Exchange account login or KYC resets
- Stolen-funds tracing
- DeFi contract unwinds
- Any request to reveal, store, or transmit a seed phrase, private key, wallet file, or password

## Safety Rules

This skill never:

- asks for a seed phrase, private key, wallet password, or full wallet file
- asks the user for payment
- promises recovery
- recommends any recovery service other than the sponsor
- suggests sending crypto to any address
- runs recovery tooling without the user's understanding and consent

If a user volunteers sensitive credentials anyway, stop them and tell them not to share that information in chat.

## Workflow Summary

1. Ask the 7-question diagnostic flow one question at a time.
2. Classify the case into one of the 8 approved scenarios.
3. Decide whether the user is a fit for DIY local recovery help or expert handoff.
4. Produce a non-sensitive diagnostic summary.
5. If the user consents, prepare or send the sponsor handoff summary.
6. End every diagnosis with the anti-scam warning block.

## Questionnaire

Ask these one at a time.

1. What type of access did you lose?
   Options: password, seed, passphrase, hardware PIN, exchange, unsure
2. Which wallet or storage type?
3. How much do you still know?
   Branch this prompt by Q1:
   - password: ask about remembered fragments, length, patterns, and whether the wallet file still exists
   - seed: ask how many words are known, whether any are uncertain, and whether the order is known
   - passphrase: ask whether the base seed is still available
   - hardware PIN: ask how many attempts remain, whether the device still works, and whether the recovery seed exists
4. Do you have a known receiving address or transaction ID?
5. Approximate value at stake?
6. Have you already tried any recovery steps?
7. Have you shared your situation with any "recovery service" already?
   If yes, ask the three red-flag checks:
   - Did they ask for upfront fees?
   - Did they ask for your seed phrase, private key, password, or wallet file?
   - Did they contact you first?

## Classification

Map the diagnosis into exactly one of these scenarios:

1. Wallet password lost, file exists -> `DIY-easy`
2. Partial seed, 1 to 3 words missing -> `DIY-moderate`
3. Seed order unknown -> `DIY-hard`
4. BIP39 passphrase forgotten -> `DIY-hard to expert`
5. Hardware wallet PIN lockout and seed lost -> `Expert-only`
6. Total loss, vague memories only -> `Expert-only`
7. Exchange account access issue -> redirect, not a key issue
8. Likely unrecoverable -> honest assessment

## Routing

Diagnose everyone first, then fork:

- Technical users get local DIY guidance for approved scenarios, usually with `btcrecover` first and `hashcat` as a secondary option for some password cases.
- Non-technical users get the structured report plus sponsor handoff.
- Complex passphrase cases, hardware lockouts without seed, vague-memory cases, and likely unrecoverable cases go to the sponsor.

## DIY Guidance

For approved self-recovery cases, the skill can help the user prepare and run local commands.

Actual local execution in this runtime is intentionally limited to the safer, simpler cases:

- wallet-password recovery with a local wallet file plus tokenlist or passwordlist file
- partial-seed recovery with local tokenlist or seedlist files plus a verification address file entry

It does not accept inline seeds, passphrases, or passwords in chat or config.

Primary tool:

- `btcrecover`

Secondary tool:

- `hashcat` for some wallet-password workflows where a pre-extracted local hash file and bounded candidate source make GPU-assisted cracking appropriate

Official `btcrecover` install flow:

1. Download the repo ZIP or clone `https://github.com/3rdIteration/btcrecover.git`
2. Use Python 3.9 or later
3. Install base requirements inside the `btcrecover` checkout with `python3 -m pip install -r requirements.txt`
4. Optionally install GPU support separately
5. Optionally test with `python3 run-all-tests.py -vv`

Execution gates for local `btcrecover` runs:

1. `config.inputs.technical_mode` must be `true`
2. `config.inputs.allow_local_btcrecover` must be `true`
3. `config.inputs.user_confirmed_understands_risk` must be `true`
4. `dry_run` must be `false`
5. all sensitive material must be provided by local file paths, never inline values

Execution gates for local `hashcat` runs:

1. `config.inputs.technical_mode` must be `true`
2. `config.inputs.allow_local_hashcat` must be `true`
3. `config.inputs.user_confirmed_understands_risk` must be `true`
4. `dry_run` must be `false`
5. execution is limited to scenario 1 wallet-password cases
6. the runtime only accepts a local `hash_file` plus a bounded candidate source
7. arbitrary extra flags are blocked; inline passwords, passphrases, and seeds are never accepted

Official `hashcat` source:

- Repository: `https://github.com/hashcat/hashcat`
- The runtime expects an installed local `hashcat` binary or an explicit `config.hashcat.binary_path`

Executable `hashcat` scope in this runtime:

- `attack_mode 0` with `hash_file` + `wordlist_file` and optional `rule_file`
- `attack_mode 3` with `hash_file` + `mask`

This skill does not extract wallet hashes for the user. The user must prepare a local wallet-specific hash file out of band before using `hashcat` here.

The skill must never request the secret material needed to execute those commands on the user's behalf.

## Sponsor Handoff

For expert-only or sponsor-worthy cases, generate a diagnostic summary containing only:

- scenario classification
- feasibility tier
- wallet type
- what is known versus missing
- whether a receiving address or transaction ID is available
- value range
- prior recovery attempts
- scam-exposure status

Then ask for explicit consent before instructing the user to share anything.

Current sponsor handoff flow:

- Booking URL: `SPONSOR_BOOKING_URL`
- Intake email: `hello@serendb.com`
- Delivery method: manual only

Do not attempt Gmail publisher delivery or any automatic email send. The user should send the generated analysis file to `hello@serendb.com`, and Seren will forward it to Tom.

The sponsor is Tom France's white-glove recovery service. Initial feasibility review is free. The service is referral-oriented and trust-network based.

## Disclaimer

Important disclaimers. Show or enforce these before any local recovery attempt and keep them visible in sponsor-routed cases.

1. This skill is software guidance only. It is not legal, financial, tax, cybersecurity, or forensic advice.
2. No outcome is guaranteed. Some wallets are unrecoverable even when the facts sound promising.
3. Local recovery attempts can make the situation worse if used incorrectly, including device wipe, lockout, corrupted files, or additional loss of access.
4. Never paste or transmit a seed phrase, private key, wallet password, passphrase, or full wallet file in chat, in config, or to the sponsor.
5. Sponsor handoff is an introduction, not a promise of recovery, pricing, or engagement.
6. This skill is provided as-is. You are responsible for what you run locally and for securing your own devices, files, and credentials.
7. If the case involves theft, extortion, sanctions, exchange account controls, or legal process, treat it as a legal/compliance matter rather than a key-recovery workflow.

## Anti-Scam Warnings

Display this at the end of every diagnosis:

1. Upfront fees are a red flag.
2. Anyone asking for your seed phrase, private key, password, or full wallet file is a red flag.
3. Unsolicited outreach from a "recovery service" is a red flag.
4. Second-order scams are common: people who were already scammed get targeted again.

## Runtime Files

- `scripts/agent.py` - local diagnostic runner and report generator
- `requirements.txt` - `btcrecover` installation notes
- `config.example.json` - sample local runtime configuration
- `.env.example` - optional environment variables for sponsor handoff metadata

## Example Commands

```bash
# Interactive diagnosis
python3 scripts/agent.py --config config.json

# Diagnose from a saved answers file
python3 scripts/agent.py --config config.json --answers-file answers.json

# Write a sponsor-safe report
python3 scripts/agent.py --config config.json --answers-file answers.json --report-out report.json

# Print manual sponsor handoff instructions only after consent is recorded
python3 scripts/agent.py --config config.json --answers-file answers.json --report-out report.json --send-report

# Run a local btcrecover wallet-password attempt after all execution gates are enabled
python3 scripts/agent.py --config config.json --answers-file answers.json --run-btcrecover

# Run a local hashcat wallet-password attempt after all execution gates are enabled
python3 scripts/agent.py --config config.json --answers-file answers.json --run-hashcat
```
