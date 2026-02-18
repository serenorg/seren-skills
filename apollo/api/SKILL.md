---
name: api
description: "Apollo.io API for people and company enrichment, prospecting, and sales intelligence"
---

# Apollo

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

## Pricing

**Pricing Model:** per_request

- Price per request: $0.04000000

**Minimum charge:** $0.00010000

---

## Need Help?

- Seren Docs: https://docs.serendb.com
- Publisher: Apollo.io API for people and company enrichment, prospecting, and sales intelligence
