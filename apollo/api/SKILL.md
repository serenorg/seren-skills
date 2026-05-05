---
name: api
description: "Apollo.io API for people and company enrichment, prospecting, and sales intelligence"
---
# Apollo API

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

Apollo.io API for people and company enrichment, prospecting, and sales intelligence

## API Endpoints

### POST `/mixed_people/api_search`

Search for people by criteria

**Example:**

```bash
curl -X POST https://api.serendb.com/publishers/apollo/mixed_people/api_search \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### POST `/people/match`

Enrich a single person record

**Example:**

```bash
curl -X POST https://api.serendb.com/publishers/apollo/people/match \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### POST `/people/bulk_match`

Bulk enrich people records

**Example:**

```bash
curl -X POST https://api.serendb.com/publishers/apollo/people/bulk_match \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### GET `/organizations/{id}`

Get organization details

**Example:**

```bash
curl -X GET https://api.serendb.com/publishers/apollo/organizations/{id} \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

### POST `/organizations/bulk_match`

Bulk enrich organizations

**Example:**

```bash
curl -X POST https://api.serendb.com/publishers/apollo/organizations/bulk_match \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### POST `/organizations/search`

Search for organizations

**Example:**

```bash
curl -X POST https://api.serendb.com/publishers/apollo/organizations/search \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### POST `/news/search`

Search news articles

**Example:**

```bash
curl -X POST https://api.serendb.com/publishers/apollo/news/search \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## API Key Setup

Before running this skill, check for an existing Seren API key in this order:

1. **Seren Desktop auth** — if the skill is running inside Seren Desktop, the runtime injects `API_KEY` automatically. Check: `echo $API_KEY`. If set, no further action is needed.
2. **Existing `.env` file** — check if `SEREN_API_KEY` is already set in the skill's `.env` file. If set, no further action is needed.
3. **Shell environment** — check if `SEREN_API_KEY` is exported in the current shell. If set, no further action is needed.

**Only if none of the above are set**, register a new agent account:

```bash
curl -sS -X POST "https://api.serendb.com/auth/agent" \
  -H "Content-Type: application/json" \
  -d '{"name":"api"}'
```

Extract the API key from the response at `.data.agent.api_key` — **this key is shown only once**. Write it to the skill's `.env` file:

```env
SEREN_API_KEY=<the-returned-key>
```

Verify:

```bash
curl -sS "https://api.serendb.com/auth/me" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

**Do not create a new account if a key already exists.** Creating a duplicate account results in a $0-balance key that overrides the user's funded account.

Reference: [https://docs.serendb.com/skills.md](https://docs.serendb.com/skills.md)

## Pricing

**Pricing Model:** per_request

- Price per request: $0.04000000

**Minimum charge:** $0.00010000

---

## Need Help?

- Seren Docs: https://docs.serendb.com
- Publisher: Apollo.io API for people and company enrichment, prospecting, and sales intelligence
