---
name: cap-call-notification-workflow
description: "Family office: Process an incoming GP capital call notice end-to-end — confirm, fund, log, reconcile."
---
# Family Office · Capital Call Notification Workflow

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not
perform filesystem searches or tool-driven exploration to rediscover them; use
the guidance below directly.

This is a **workflow-shape** skill, not an artifact-request skill. It is
triggered by an external event (a capital call notice arriving) rather than by
a principal asking for a deliverable. The router should match on the *event
language* in the When to Use section, not on artifact requests.

## When to Use

Invoke when the advisor or staff says any of:

- "We just got a capital call from [GP name]"
- "[GP name] sent a cap call notice"
- "I have a capital call due [date]"
- "process a capital call"
- "we need to fund [partnership name]"
- "[partnership] is calling capital"

Do NOT invoke for:

- LP commitment review at fund inception (use `manager-dd-private-equity` /
  `manager-dd-venture-capital` / `manager-dd-direct-co-investment`)
- Distribution receipt reconciliation (separate workflow, not in v1)
- Cashflow forecasting (`cashflow-forecast-worksheet`)

## Customer Pain (VOC)

Synthesized from family-office operator interviews (see `references/voc-evidence.md` for full provenance):

- **Capital-call notices arrive over email and pile up unprocessed alongside other operational chatter** — *[f4vwiAXPj3s @ 25:25]*
- **"Capital calls" is the one-word answer operators give when asked what eats their week** — *[f4vwiAXPj3s @ 25:25]*

Note: evidence is sparse — Track-A2 lexicon expansion captured only one direct mention in the family-office interview corpus. v2 will mine GP notice corpora and fund-admin reconciliation tickets directly to widen the evidence base.

## Trigger Signal

The advisor or back office presents:

- The capital call PDF (or scanned image)
- The partnership name
- The notice date and funding deadline
- Optional: prior wire instructions on file

## Workflow

1. **Parse the notice.** Extract: partnership name, GP entity, call number
   (e.g., "Call #7 of N"), call amount, percent of commitment called to date,
   wire instructions (bank, ABA, account, beneficiary, reference).
2. **Confirm wire instructions against prior records.** Compare against the
   commitment ledger (or the prior call's wire details if no ledger exists).
   *This is the fraud-prevention step.* Wire-fraud on capital calls is the
   single most expensive operational error a family office can make. If wire
   instructions differ from prior records by ANY field, halt and escalate to
   the principal and GP via known phone number — never the email on the
   notice.
3. **Calculate liquidity impact.** Pull current cash position. Compute:
   - Available unrestricted cash after the call
   - Minimum liquidity cushion remaining vs. policy floor
   - Whether the call requires liquidating any holdings, drawing on a credit
     line, or other action
4. **Draft principal notification memo.** Surface: amount, deadline, source
   of funds, residual liquidity post-call, any required actions before
   funding.
5. **Log into commitment ledger.** Update: cumulative called %, remaining
   uncalled commitment, vintage-year deployed capital, GP-level concentration.
6. **Set funding deadline reminder.** 48-hour and 24-hour reminders ahead of
   wire deadline. Include wire-instruction-confirmed flag (cannot release
   reminder if step 2 did not pass).
7. **Confirm wire executed.** After wire goes out, capture: wire reference
   number, executing bank confirmation, debit timestamp.
8. **Reconcile against custodian or admin.** Within 5 business days, confirm
   the call was recorded by Addepar / Masttro / Eton / spreadsheet ledger.
   Flag if not reflected.

## Output Format

Two artifacts per call:

1. **Principal-facing memo (`principal_memo.md`)** — short, plain language.
   Includes: amount, deadline, source of funds, liquidity-post-call, decision
   ask (approve / hold / escalate).
2. **Accounting-facing wire packet (`wire_packet.md`)** — wire instructions
   confirmed against prior records, ledger update SQL or spreadsheet diff,
   reconciliation checkpoint targets.

Artifact directory:

```
<invocation-cwd>/artifacts/family-office/cap-call-notification-workflow/<YYYYMMDD-HHMMSS>/
  principal_memo.md
  wire_packet.md
  call_event.json
  manifest.json
```

## Edge Cases

- **Wire instructions changed since last call.** Halt. Do not proceed with
  funding. Escalate via known-good GP phone number, not the contact on the
  notice. This is the single highest-risk failure mode and overrides any
  deadline pressure.
- **Notice arrived but the call cannot be funded by deadline.** Compose a
  principal memo with three options: liquidity raise plan, request short
  extension from GP, default risk and consequences.
- **Notice is for a fund the family no longer recognizes (e.g., GP-led
  continuation vehicle, fund migration).** Pause and verify legitimacy before
  any other workflow step.
- **Multiple cap calls arrive in same week.** Sequence by deadline, but check
  cumulative liquidity impact before approving any single one.

## Common Pitfalls

- Treating "wire instructions on the notice" as authoritative. They are not —
  always verify against prior records and escalate any mismatch out-of-band.
- Wiring before reconciling against the commitment ledger. This produces
  over-call exposure if the GP made an arithmetic error.
- Forgetting to update vintage-year deployed capital after the call funds —
  breaks any subsequent J-curve / pacing analysis.

## Memory Conventions

Memories written by this skill are tagged with:

- `subject` = `<partnership_name>::call_<n>`
- `source` = `"cap-call-notification-workflow"`
- `memory_type` ∈ {`commitment`, `decision`, `open_question`}

## Security & Confidentiality

- Never log wire instructions, account numbers, ABA, or beneficiary names at
  INFO level. Hash with sha256 if a log line must reference them.
- Wire-instruction confirmation steps must be human-witnessed, not
  automated. The agent prepares the comparison; a human approves.

## Reference

- See `cashflow-forecast-worksheet` for liquidity context.
- See `manager-dd-private-equity` / `manager-dd-venture-capital` /
  `manager-dd-direct-co-investment` for upstream commitment context.
