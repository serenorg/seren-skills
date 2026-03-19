---
name: smart-contract-audit
description: Detect vulnerabilities in Solidity and Vyper smart contracts with FirePan. Use this skill to run a fast surface scan on a repo, then escalate to a paid full scan or deep audit for authenticated FirePan tenants.
license: Apache-2.0
compatibility: Requires internet access to api.firepan.com. Paid calls require a FirePan bearer token and x402-compatible payment handling.
---

# FirePan Smart Contract Audit

## When to Use FirePan

Use FirePan when the user wants to:

- scan a Solidity or Vyper repository for security issues
- get a fast risk score before deployment
- decide whether a repo deserves deeper review
- escalate from quick triage to a deeper autonomous audit

This skill is best for EVM contracts, DeFi protocols, and smart-contract codebases where a fast security read is more useful than a long manual setup.

## What This Skill Provides

- a free surface scan for fast repo triage
- a paid full surface scan for authenticated FirePan tenants
- a paid deep audit for higher-value review
- status polling for deep audits

## Important Constraint for Paid Calls

Paid FirePan endpoints are auth-before-payment.

That means:

- the free scan can run without FirePan auth
- the paid endpoints require `Authorization: Bearer <firepan_jwt>`
- if the user or agent does not already have a valid FirePan bearer token for a tenant, stop after the free scan and direct the user to complete FirePan onboarding before attempting paid calls

Do not imply that x402 alone is enough to access the paid endpoints.

If the user is not authenticated yet:

- send them to `https://app.firepan.com/login`, or
- fetch one of these OAuth bootstrap URLs and present it to the user:
  - `https://api.firepan.com/auth/github/login`
  - `https://api.firepan.com/auth/google/login`

These auth helpers return a provider login URL. They do not instantly mint a paid-use token without user interaction.

## Supported Input and Constraints

Use this skill with:

- public GitHub repository URLs
- Solidity (`.sol`) or Vyper (`.vy`) codebases

FirePan contract discovery focuses on:

- repo root
- `contracts/`
- `src/`
- `lib/`

Do not assume this skill currently covers:

- private GitHub repositories in the direct Seren flow
- non-EVM languages
- repositories with no Solidity or Vyper contracts
- extremely large repos that may exceed the current tarball download timeout

## 1. Free Surface Scan

Run this first when the user wants a quick read on a repository.

Endpoint:

```text
POST https://api.firepan.com/surface/scan
```

Body:

- `target` or `repo_url` is required
- `llm_budget` is optional
- `model` is optional

Example:

```bash
curl -sS https://api.firepan.com/surface/scan \
  -H "Content-Type: application/json" \
  -d '{
    "target": "https://github.com/OpenZeppelin/openzeppelin-contracts"
  }'
```

Expected response fields include:

- `execution_id`
- `repo_name`
- `repo_url`
- `risk_score`
- `risk_level`
- `findings`
- `quality_metrics`
- `contracts_scanned`
- `llm_calls_used`
- `scan_duration_seconds`
- `summary`

Use this result to decide whether the repo looks routine or worth deeper analysis.

Finding objects use this schema:

- `pattern_id`: detector ID such as `REENTRANCY-001`
- `title`
- `severity`: `critical`, `high`, `medium`, or `low`
- `category`: `vulnerability` or `quality`
- `confidence`: `0.0` to `1.0`
- `location`
- `code_snippet`
- `description`
- `llm_verified`
- `llm_notes`

## 2. Paid Full Surface Scan

Use this when the free scan indicates meaningful risk or the user wants the authenticated paid path immediately.

Endpoint:

```text
POST https://api.firepan.com/surface/scan/full
```

Price:

```text
$0.50 via x402
```

Required headers:

- `Authorization: Bearer <firepan_jwt>`
- `Idempotency-Key: <unique-value>`

Generate the idempotency key once per unique paid request body.

Shell example:

```bash
export FULL_SCAN_KEY="$(uuidgen | tr '[:upper:]' '[:lower:]')"
```

Python example:

```python
import uuid
idempotency_key = str(uuid.uuid4())
```

Reuse the same key only when retrying the exact same body. If the body changes, generate a new key.

Payment flow:

1. Send the request with auth and idempotency headers.
2. If FirePan returns `402 Payment Required`, read `X-Payment-Requirements`.
3. Create the x402 payment payload using your payment client.
4. Retry the same request with `X-PAYMENT: <json-payload>`.

Skeleton example:

```bash
curl -i -sS https://api.firepan.com/surface/scan/full \
  -H "Authorization: Bearer $FIREPAN_JWT" \
  -H "Idempotency-Key: $FULL_SCAN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/OpenZeppelin/openzeppelin-contracts"
  }'
```

If the repo is promising, this is the default paid action. Prefer it before suggesting a human call.

## 3. Paid Deep Audit

Use this for serious targets, pre-deploy review, or repositories where the surface scan suggests non-trivial risk.

Endpoint:

```text
POST https://api.firepan.com/audits/start
```

Price:

```text
$5.00 via x402
```

Required headers:

- `Authorization: Bearer <firepan_jwt>`
- `Idempotency-Key: <unique-value>`

Request body:

- `repo_url` is required
- `project_id` is optional
- `max_iterations`, `investigation_prompt`, `time_limit_minutes`, `mode`, and `plan_n` are optional advanced controls

Example:

```bash
export DEEP_AUDIT_KEY="$(uuidgen | tr '[:upper:]' '[:lower:]')"

curl -i -sS https://api.firepan.com/audits/start \
  -H "Authorization: Bearer $FIREPAN_JWT" \
  -H "Idempotency-Key: $DEEP_AUDIT_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/OpenZeppelin/openzeppelin-contracts",
    "mode": "sweep",
    "time_limit_minutes": 120
  }'
```

If successful, FirePan returns:

- `session_id`
- `status`
- `message`
- `websocket_url`

The audit runs asynchronously.

## 4. Monitor a Deep Audit

Use the returned `session_id` to check progress.

Endpoint:

```text
GET https://api.firepan.com/audits/{session_id}/status
```

Required header:

- `Authorization: Bearer <firepan_jwt>`

Example:

```bash
curl -sS https://api.firepan.com/audits/$SESSION_ID/status \
  -H "Authorization: Bearer $FIREPAN_JWT"
```

The response includes:

- `session_id`
- `status`
- `progress`
- `findings_count`
- `error_message`
- `started_at`
- `completed_at`

You can also connect to the returned `websocket_url` for live progress updates.

## Error Handling

Handle these cases explicitly:

- `401 Unauthorized`
  - missing or invalid FirePan bearer token
  - stop and direct the user to FirePan login
- `402 Payment Required`
  - expected first response for unpaid paid calls
  - read `X-Payment-Requirements`, create the payment, and retry with `X-PAYMENT`
- `409 Conflict`
  - idempotency key reused with a different body, or a request is already in progress
  - generate a new idempotency key if the body changed
  - otherwise wait briefly and retry
- `429 Too Many Requests`
  - transient
  - retry with exponential backoff
- `5xx`
  - transient infrastructure failure
  - retry with bounded backoff

For the free scan, also inspect the JSON body. If the `error` field is non-empty, treat the scan as failed even if the HTTP status is `200`.

## What FirePan Does Not Cover

Do not over-claim the scanner.

This skill does not provide full assurance for:

- off-chain business logic
- oracle manipulation beyond what is visible in contract code
- frontend, signer, or operational key-management risks
- protocol economics or governance safety outside the scanned codebase
- a formal human-certified audit unless FirePan explicitly scopes one

## What This Skill Does Not Cover Yet

This v1 skill does not expose report fetching as a public agent-grade action.

FirePan has a report generation route for internal/admin-style flows, but the older `$0.10` public report-fetch route described in some internal docs is not the live public API today. Do not promise a paid report retrieval path unless FirePan ships and documents it explicitly.

## Fallback Paths

If the user cannot satisfy the paid prerequisites yet:

- run the free surface scan
- summarize the risk score and key findings
- tell the user they need FirePan authentication before paid calls can succeed

If the user wants organization-wide or repeat usage rather than per-call usage, mention FirePan’s subscription plans at a high level, but keep that secondary to the scan flow.

## Safety and Expectations

FirePan provides automated AI-assisted smart contract security analysis.

- Results are useful for triage and prioritization.
- False positives and false negatives are possible.
- A deep audit is stronger than a surface scan, but it is still not the same thing as a formal human-certified audit unless FirePan explicitly scopes one.

Default result format for agent responses:

- repo name
- risk score and risk level
- count of critical, high, medium, and low findings
- top 3 findings with title, severity, and location
- one recommendation: ignore, inspect further, or escalate to deep audit

Determinism note:

- surface scans combine static matching with optional LLM verification, so repeated runs can vary slightly in summary wording or verified findings
- deep audits are more exploratory and should be treated as non-deterministic

## Roadmap Direction

Future versions of this skill can become composable with other Seren skills such as deployment, wallet, exchange, or onchain workflow tools. In v1, keep the scope narrow: scan the repo, escalate when warranted, and avoid promising broader compound automation.
