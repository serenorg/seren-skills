---
name: publisher-factory
description: "Create Seren API integration publishers for a company, its top competitors, and adjacent category leaders after live catalog checks and verified API research."
version: 1.0.0
tags: [seren, publishers, integrations, api-research]
---

# Publisher Factory

## For Claude: How to Use This Skill

Use this skill when an operator asks to create or refresh Seren publishers for
a company, a SaaS category, or a competitive set. The skill is review-and-build
automation for publisher definitions: it verifies what exists, researches only
official API surfaces, and deploys validated publishers using the live Asana
publisher as the commercial template.

## When to Use

- create a publisher for a company
- create publishers for a product category
- generate API integration publishers for competitors
- clone the Asana publisher pattern for another SaaS/API company
- build publishers for adjacent category leaders

## Mandatory Live Catalog Guard

Before saying any third-party service is available or unavailable, call
`list_agent_publishers` with no arguments. Do this on every run. The publisher
catalog is live and changes frequently.

After the empty-argument catalog call:

1. Call `list_agent_publishers` with the target company slug or search term.
2. Call `list_agent_publishers` with `slug: "asana"` and use that live Asana
   publisher as the source template.
3. If a candidate publisher already exists, report it under `existing` unless
   the operator explicitly asked to update it.
4. Never rely on memory, stale local docs, or prior runs to decide publisher
   availability.

## Required Inputs

Ask for missing inputs only when needed:

- target company or product category
- whether existing publishers may be updated
- preferred naming convention if the operator has one

Default discovery scope:

- include the target company
- include the top 10 competitors
- include adjacent category leaders until the run reaches 20 companies total

## Research Workflow

For each candidate company:

1. Identify the official website and official public API docs.
2. Verify API availability, auth style, base URLs, endpoint families, and rate
   limits using the Perplexity Seren publisher.
3. Prefer official documentation over blog posts, SDK examples, generated
   snippets, and reverse-engineered browser traffic.
4. Skip the company if public API docs cannot be found or API access is
   unclear.
5. Record the skip reason in the inline report.

Do not create publishers from unofficial endpoints, hidden browser APIs,
private partner APIs, invite-only APIs, or undocumented routes.

## Publisher Template Rules

Clone the live Asana publisher exactly for commercial and ownership settings:

- pricing fields
- owner and organization fields
- x402 wallet address and wallet network/Base network settings
- contact and support metadata where applicable
- prepaid and onchain billing settings
- minimum balance and low-balance thresholds

Do not hard-code old Asana values. Read the live Asana publisher during the run
and copy the relevant values into each new publisher.

## Generated Publisher Contract

Every generated publisher must include:

```yaml
integration_type: api
publisher_category: integration
billing_model: x402_per_request
default_response_format: json
undocumented_endpoint_policy: default_deny
```

Every generated publisher must also include:

- clear capability summaries
- usage examples
- resource descriptions
- auth documentation
- endpoint catalog
- protected destructive endpoints

Auth support:

- Use OAuth when the official API supports OAuth.
- Use API key auth when the official API uses token or key based access.
- If both OAuth and API key auth are supported, include both and mark the
  preferred official flow.

Endpoint catalog rules:

- Include read, write, update, and delete endpoints when the official API
  documents them.
- Mark destructive endpoints such as delete, revoke, archive, cancel, remove,
  purge, or disable as `protected`.
- Default deny any endpoint not explicitly catalogued.

Logo rules:

- Use an official 200 x 200 logo when available.
- Missing official logo is non-blocking.
- Continue deployment with `logo_status: missing` when no compliant logo is
  available.

## Deployment Gates

Deploy or update only after all required gates pass:

1. Live publisher catalog was queried with no arguments.
2. Existing publisher status was checked for the candidate.
3. The live Asana publisher was loaded as the template.
4. Official API docs were found.
5. Perplexity verification passed.
6. Authentication method is known.
7. Endpoint catalog is populated from official docs.
8. Destructive endpoints are marked protected.
9. Basic health check passes, or the official docs provide enough static
   metadata to create a non-callable blocked stub.

If any gate fails, block or skip the candidate. Do not deploy partial publisher
definitions with guessed auth, guessed endpoints, or guessed billing metadata.

## Output Format

Return a concise inline report with these groups:

- `deployed`: publishers created in this run
- `existing`: matching publishers already present in the live catalog
- `updated`: existing publishers changed in this run
- `skipped`: researched candidates intentionally skipped
- `blocked`: candidates that could not be safely evaluated or deployed

For each item, include:

- company name
- publisher slug
- official docs URL when available
- auth method
- endpoint family count
- logo status
- validation status
- short reason or next action

## Persistence Rules

Do not persist research notes, candidate lists, or run state. The only durable
output is the generated or updated publisher definition. Return the run report
inline.

## Safety Rules

- Do not claim a third-party integration is unavailable without the mandatory
  live catalog guard.
- Do not create publishers for companies whose APIs are private, invite-only,
  undocumented, or legally unclear.
- Do not invent pricing, wallet, owner, contact, or Base network settings.
  Clone the live Asana values for those fields.
- Do not persist anything except the generated publisher definitions.
- Do not deploy a publisher that allows uncatalogued endpoints.

## Example

Operator request:

```text
Create publishers for Linear and similar project management tools.
```

Expected behavior:

1. Call `list_agent_publishers` with no arguments.
2. Check for an existing `linear` publisher.
3. Resolve the live Asana publisher template.
4. Research Linear, top 10 competitors, and adjacent category leaders, capped at
   20 companies total.
5. Verify each API with Perplexity.
6. Deploy only validated publishers.
7. Return the grouped inline report.
