---
name: seren-cloud
description: "Deploy and operate hosted skills through the first-class seren-cloud publisher."
---
# Seren Cloud

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

Use this skill when a user wants to deploy or run skills in Seren-managed cloud runtime.

## API

Use this skill alongside the core Seren API skill (`https://api.serendb.com/skill.md`).

## Base Route

All routes go through `https://api.serendb.com/publishers/seren-cloud`.

## Authentication

All endpoints require `Authorization: Bearer $SEREN_API_KEY`.

## Deployments

Deploy new agents and manage deployment lifecycle operations.

Deploy a skill to seren-cloud.

### POST `/publishers/seren-cloud/deploy`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-cloud/deploy" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"code_bundle_base64":"","mode":"","name":"my-agent","skill_slug":"my-publisher"}'
```

List all deployments for the authenticated organization.

### GET `/publishers/seren-cloud/deployments`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/deployments" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Get details of a specific deployment.

### GET `/publishers/seren-cloud/deployments/{id}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/deployments/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Delete a deployment and clean up compute resources.

### DELETE `/publishers/seren-cloud/deployments/{id}`

```bash
curl -sS -X DELETE "https://api.serendb.com/publishers/seren-cloud/deployments/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Update config and/or secrets without redeploying code.

### PATCH `/publishers/seren-cloud/deployments/{id}`

```bash
curl -sS -X PATCH "https://api.serendb.com/publishers/seren-cloud/deployments/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"config":{},"secrets":{}}'
```

Get logs for a deployment.

### GET `/publishers/seren-cloud/deployments/{id}/logs`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/deployments/<id>/logs" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Start an always-on deployment (scale to 1 replica).

### POST `/publishers/seren-cloud/deployments/{id}/start`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-cloud/deployments/<id>/start" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Stop an always-on deployment (scale to 0 replicas).

### POST `/publishers/seren-cloud/deployments/{id}/stop`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-cloud/deployments/<id>/stop" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

## Runs

Execute one-off runs and inspect run history, logs, and artifacts.

List run events (history) for a deployment.

### GET `/publishers/seren-cloud/deployments/{id}/runs`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/deployments/<id>/runs" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Run a one-shot invocation of a deployment.

### POST `/publishers/seren-cloud/deployments/{id}/runs`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-cloud/deployments/<id>/runs" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Get a single run event for a deployment.

### GET `/publishers/seren-cloud/deployments/{id}/runs/{run_id}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/deployments/<id>/runs/<run_id>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

List artifacts emitted by a specific deployment run.

### GET `/publishers/seren-cloud/deployments/{id}/runs/{run_id}/artifacts`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/deployments/<id>/runs/<run_id>/artifacts" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Cancel a queued/running deployment run.

### POST `/publishers/seren-cloud/deployments/{id}/runs/{run_id}/cancel`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-cloud/deployments/<id>/runs/<run_id>/cancel" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Stream updates for a deployment run as Server-Sent Events.

### GET `/publishers/seren-cloud/deployments/{id}/runs/{run_id}/stream`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/deployments/<id>/runs/<run_id>/stream" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

List all runs across all deployments for the organization.

### GET `/publishers/seren-cloud/runs`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/runs" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Get a single run event by ID (cross-agent).

### GET `/publishers/seren-cloud/runs/{run_id}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/runs/<run_id>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

List artifacts emitted by a run event.

### GET `/publishers/seren-cloud/runs/{run_id}/artifacts`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/runs/<run_id>/artifacts" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Cancel a queued/running run by ID.

### POST `/publishers/seren-cloud/runs/{run_id}/cancel`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-cloud/runs/<run_id>/cancel" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Stream updates for a run as Server-Sent Events.

### GET `/publishers/seren-cloud/runs/{run_id}/stream`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/runs/<run_id>/stream" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

## Environments

Manage reusable runtime environments for deployments.

List reusable execution environment profiles.

### GET `/publishers/seren-cloud/environments`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/environments" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Create a reusable execution environment profile.

### POST `/publishers/seren-cloud/environments`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-cloud/environments" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"docker_image":"","name":"my-agent"}'
```

Get a reusable execution environment profile.

### GET `/publishers/seren-cloud/environments/{id}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-cloud/environments/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Delete a reusable execution environment profile.

### DELETE `/publishers/seren-cloud/environments/{id}`

```bash
curl -sS -X DELETE "https://api.serendb.com/publishers/seren-cloud/environments/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Update a reusable execution environment profile.

### PATCH `/publishers/seren-cloud/environments/{id}`

```bash
curl -sS -X PATCH "https://api.serendb.com/publishers/seren-cloud/environments/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"description":"","docker_image":"","is_default":true}'
```
