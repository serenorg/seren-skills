---
name: api
description: "Use Seren API directly for agent registration/authentication, account recovery, wallet funding, publisher discovery/execution, payment capability checks, and billing endpoints across api.serendb.com."
---

# Seren API

Use this skill whenever a user needs direct platform-level Seren API calls (not just one publisher).

Canonical references:
- Skill doc: `https://api.serendb.com/skill.md`
- OpenAPI: `https://api.serendb.com/openapi.json`
- Base URL: `https://api.serendb.com`

## 1. Registration

Register an agent and receive an API key (shown once).

### POST `/auth/agent`

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Optional named registration:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-agent"}'
```

The current response shape provides the key at `.data.agent.api_key`.

### GET `/auth/me`

Check the current authenticated agent's identity:

```bash
curl -sS "https://api.serendb.com/auth/me" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### PATCH `/auth/agent`

```bash
curl -sS -X PATCH "https://api.serendb.com/auth/agent" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com"}'
```

A verified email is required before Stripe deposits. Updating the email sends a verification link — the user clicks the link to verify, no separate API call is needed.

## 2. Authentication

All authenticated requests require your API key in the `Authorization` header.

```bash
export SEREN_API_KEY="seren_<your_key>"
```

### Authenticated request pattern

```bash
curl -sS "https://api.serendb.com/wallet/balance" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Your API key is shown once at registration. If you lose it, use your recovery code (see Account Recovery).

### Credential storage

The `seren` CLI stores credentials at:
- macOS/Linux: `~/.config/seren/credentials.toml`
- Windows: `%APPDATA%\\seren\\credentials.toml`

You can also store your key in environment variables or your agent's memory/secrets store.

### Helper scripts

The [`seren/api` skill package](https://github.com/serenorg/seren-skills/tree/main/seren/api/scripts) includes wrapper scripts that auto-resolve credentials and simplify API calls:

```bash
# Bash/zsh — loads SEREN_API_KEY from env, credentials.toml, or mints a new key
eval "$(scripts/resolve_credentials.sh)"
scripts/seren_api.sh get /wallet/balance
scripts/seren_api.sh post /wallet/recovery --data '{}'
```

```powershell
# PowerShell equivalent
.\scripts\resolve_credentials.ps1
.\scripts\seren_api.ps1 get /wallet/balance
```

Supported overrides:
- `SEREN_API_HOST` (default `https://api.serendb.com`)
- `SEREN_CREDENTIALS_FILE` (explicit file path)
- `SEREN_AUTO_CREATE_KEY=0` (disable auto-mint)

## 3. Account Recovery

Essential recovery flow:

1. Set up recovery using authenticated endpoint:

### POST `/wallet/recovery`

```bash
curl -sS -X POST "https://api.serendb.com/wallet/recovery" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com"}'
```

Save the `recovery_code` from the response — it is shown once and cannot be retrieved later.

2. Recover after key loss using recovery code:

### POST `/wallet/recover`

```bash
curl -sS -X POST "https://api.serendb.com/wallet/recover" \
  -H "Content-Type: application/json" \
  -d '{"recovery_code":"ABCDEFGHJKLMNPQRSTUVWX23"}'
```

`/wallet/recover` rotates credentials and recovery code; save returned secrets immediately.

## 4. Wallet and Funding

Essential wallet endpoints:

- `GET /wallet/balance`
- `POST /wallet/deposit` (Stripe checkout, $100 max, requires verified email)
- `GET /wallet/transactions`
- `GET /wallet/daily/eligibility`
- `POST /wallet/daily/claim`
- `POST /wallet/bonus/payment-method`
- `POST /wallet/bonus/signup`
- `POST /wallet/deposit/crypto` (x402-style two-step payment)
- `GET /wallet/referral`
- `POST /wallet/referral/apply`

Examples:

### GET `/wallet/balance`

```bash
curl -sS -X GET "https://api.serendb.com/wallet/balance" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### POST `/wallet/deposit`

```bash
curl -sS -X POST "https://api.serendb.com/wallet/deposit" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"amount_cents":500}'
```

### GET `/wallet/transactions`

```bash
curl -sS -X GET "https://api.serendb.com/wallet/transactions" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### GET `/wallet/daily/eligibility`

```bash
curl -sS -X GET "https://api.serendb.com/wallet/daily/eligibility" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### POST `/wallet/daily/claim`

```bash
curl -sS -X POST "https://api.serendb.com/wallet/daily/claim" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### POST `/wallet/bonus/payment-method`

```bash
curl -sS -X POST "https://api.serendb.com/wallet/bonus/payment-method" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### POST `/wallet/bonus/signup`

```bash
curl -sS -X POST "https://api.serendb.com/wallet/bonus/signup" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### POST `/wallet/deposit/crypto`

```bash
curl -sS -X POST "https://api.serendb.com/wallet/deposit/crypto" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"amount":"10.50","publisher_id":"<publisher_uuid>"}'
```

If HTTP `402` is returned, follow payment instructions and retry with payment headers.

### GET `/wallet/referral`

```bash
curl -sS -X GET "https://api.serendb.com/wallet/referral" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### POST `/wallet/referral/apply`

```bash
curl -sS -X POST "https://api.serendb.com/wallet/referral/apply" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"referral_code":"<code>"}'
```

## 5. Publishers

Publisher discovery and execution endpoints:

- `GET /publishers`
- `GET /publishers/suggest`
- `GET /publishers/{slug}`
- `POST /publishers/{slug}`
- `POST /publishers/{slug}/estimate`
- `GET /publishers/{slug}/logo`
- `GET /publishers/{slug}/{path}`
- `POST /publishers/{slug}/{path}`
- `GET /payments/supported`

### GET `/publishers`

```bash
curl -sS -X GET "https://api.serendb.com/publishers"
```

### GET `/publishers/suggest`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/suggest?query=web+scraping"
```

### GET `/publishers/{slug}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/<slug>"
```

### POST `/publishers/{slug}`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/<slug>" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### POST `/publishers/{slug}/estimate`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/<slug>/estimate" \
  -H "Content-Type: application/json" \
  -d '{"publisher_id":"<publisher_uuid>","query":"SELECT 1"}'
```

### GET `/publishers/{slug}/logo`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/<slug>/logo"
```

### GET `/payments/supported`

```bash
curl -sS -X GET "https://api.serendb.com/payments/supported"
```

### GET `/publishers/{slug}/{path}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/<slug>/<path>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### POST `/publishers/{slug}/{path}`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/<slug>/<path>" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### MCP access via publisher proxy

- List tools: `GET /publishers/{slug}/_tools`
- List resources: `GET /publishers/{slug}/_resources`
- Call tool: `POST /publishers/{slug}/{tool_name}`
- Read resource: `GET /publishers/{slug}/{resource_path}`

## 6. Billing

Essential billing endpoints:

- `POST /billing/invoices/{id}/pay`
- `GET /billing/invoices/{id}/payments`
- `GET /billing/payment-methods`
- `POST /billing/payment-methods`
- `DELETE /billing/payment-methods/{id}`

### POST `/billing/invoices/{id}/pay`

```bash
curl -sS -X POST "https://api.serendb.com/billing/invoices/<id>/pay" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### GET `/billing/invoices/{id}/payments`

```bash
curl -sS -X GET "https://api.serendb.com/billing/invoices/<id>/payments" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### GET `/billing/payment-methods`

```bash
curl -sS -X GET "https://api.serendb.com/billing/payment-methods" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### POST `/billing/payment-methods`

```bash
curl -sS -X POST "https://api.serendb.com/billing/payment-methods" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"set_as_default":true,"stripe_payment_method_id":"pm_..."}'
```

### DELETE `/billing/payment-methods/{id}`

```bash
curl -sS -X DELETE "https://api.serendb.com/billing/payment-methods/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

## 7. Response Handling

Many Seren responses are wrapped. Use tolerant parsing:

```bash
jq '.data // .body // .'
```
