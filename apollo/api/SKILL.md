---
name: api
description: "Apollo.io API for people and company enrichment, prospecting, and sales intelligence"
---

# Apollo

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

If `SEREN_API_KEY` is not set (and the skill is not running inside Seren Desktop, which injects `API_KEY` automatically), the agent must register and provision a key before proceeding:

1. Register a new Seren agent account:

   ```bash
   curl -sS -X POST "https://api.serendb.com/auth/agent" \
     -H "Content-Type: application/json" \
     -d '{"name":"api"}'
   ```

2. Extract the API key from the response at `.data.agent.api_key`. **This key is shown only once** — save it immediately.

3. Write the key to the skill's `.env` file:

   ```env
   SEREN_API_KEY=<the-returned-key>
   ```

4. Verify the key works:

   ```bash
   curl -sS "https://api.serendb.com/auth/me" \
     -H "Authorization: Bearer $SEREN_API_KEY"
   ```

Do not show a generic "missing key" error. Always auto-register and provision the key so the skill can proceed. If the user declines registration, respect that and stop.

Reference: [https://docs.serendb.com/skills.md](https://docs.serendb.com/skills.md)

## Pricing

**Pricing Model:** per_request

- Price per request: $0.04000000

**Minimum charge:** $0.00010000

---

## Need Help?

- Seren Docs: https://docs.serendb.com
- Publisher: Apollo.io API for people and company enrichment, prospecting, and sales intelligence
