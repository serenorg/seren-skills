---
name: seren-db
description: "Create, manage, and query SerenDB databases through the first-class seren-db publisher."
---

# Seren DB

Use this skill when a user wants to create, manage, or query SerenDB serverless Postgres databases.

## API

Use this skill alongside the core Seren API skill (`https://api.serendb.com/skill.md`).

## Base Route

All routes go through `https://api.serendb.com/publishers/seren-db`.

## Authentication

All endpoints require `Authorization: Bearer $SEREN_API_KEY`.

## Projects

Manage SerenDB projects and fetch project-level connection URIs.

List all projects in the organization.

### GET `/publishers/seren-db/projects`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-db/projects" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Create a new SerenDB project.

### POST `/publishers/seren-db/projects`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/projects" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-agent","region":""}'
```

Get a project by ID.

### GET `/publishers/seren-db/projects/{id}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-db/projects/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Delete a project.

### DELETE `/publishers/seren-db/projects/{id}`

```bash
curl -sS -X DELETE "https://api.serendb.com/publishers/seren-db/projects/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Update a project.

### PATCH `/publishers/seren-db/projects/{id}`

```bash
curl -sS -X PATCH "https://api.serendb.com/publishers/seren-db/projects/<id>" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"block_public_connections":true,"block_vpc_connections":true,"compute_unit_max":0}'
```

Get connection URI for a project.

### GET `/publishers/seren-db/projects/{id}/connection_uri`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-db/projects/<id>/connection_uri" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

## Branches

Create, inspect, rename, delete, and set default branches.

List all branches in a project.

### GET `/publishers/seren-db/projects/{id}/branches`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-db/projects/<id>/branches" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Create a new branch in a project.

### POST `/publishers/seren-db/projects/{id}/branches`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/projects/<id>/branches" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-agent"}'
```

Get a specific branch.

### GET `/publishers/seren-db/projects/{id}/branches/{bid}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Delete a branch.

### DELETE `/publishers/seren-db/projects/{id}/branches/{bid}`

```bash
curl -sS -X DELETE "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Rename a branch.

### PATCH `/publishers/seren-db/projects/{id}/branches/{bid}`

```bash
curl -sS -X PATCH "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-agent"}'
```

Set a branch as the default for its project.

### POST `/publishers/seren-db/projects/{id}/branches/{bid}/set-default`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/set-default" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

## Databases

Manage databases within a specific branch.

List all databases on a branch.

### GET `/publishers/seren-db/projects/{id}/branches/{bid}/databases`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/databases" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Create a new database on a branch.

### POST `/publishers/seren-db/projects/{id}/branches/{bid}/databases`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/databases" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-agent"}'
```

Get a specific database.

### GET `/publishers/seren-db/projects/{id}/branches/{bid}/databases/{did}`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/databases/<did>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Update a database (change owner).

### PUT `/publishers/seren-db/projects/{id}/branches/{bid}/databases/{did}`

```bash
curl -sS -X PUT "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/databases/<did>" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"owner_name":"my-agent"}'
```

Delete a database.

### DELETE `/publishers/seren-db/projects/{id}/branches/{bid}/databases/{did}`

```bash
curl -sS -X DELETE "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/databases/<did>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

## Roles

Create, list, delete, and reset database roles.

List all roles on a branch.

### GET `/publishers/seren-db/projects/{id}/branches/{bid}/roles`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/roles" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Create a new role on a branch.

### POST `/publishers/seren-db/projects/{id}/branches/{bid}/roles`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/roles" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-agent"}'
```

Delete a role from a branch.

### DELETE `/publishers/seren-db/projects/{id}/branches/{bid}/roles/{rid}`

```bash
curl -sS -X DELETE "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/roles/<rid>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Reset a role's password.

### POST `/publishers/seren-db/projects/{id}/branches/{bid}/roles/{rid}/reset_password`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/roles/<rid>/reset_password" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

## Compute Endpoints

Manage branch compute endpoints and lifecycle operations.

List all endpoints on a branch.

### GET `/publishers/seren-db/projects/{id}/branches/{bid}/endpoints`

```bash
curl -sS -X GET "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/endpoints" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Create a new compute endpoint on a branch.

### POST `/publishers/seren-db/projects/{id}/branches/{bid}/endpoints`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/endpoints" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"autoscaling_max":0,"autoscaling_min":0,"compute_unit":""}'
```

Delete an endpoint.

### DELETE `/publishers/seren-db/projects/{id}/branches/{bid}/endpoints/{eid}`

```bash
curl -sS -X DELETE "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/endpoints/<eid>" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Update an endpoint's settings.

### PATCH `/publishers/seren-db/projects/{id}/branches/{bid}/endpoints/{eid}`

```bash
curl -sS -X PATCH "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/endpoints/<eid>" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"autoscaling_max":0,"autoscaling_min":0,"pooler_enabled":true}'
```

Start a suspended endpoint.

### POST `/publishers/seren-db/projects/{id}/branches/{bid}/endpoints/{eid}/start`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/endpoints/<eid>/start" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

Stop (suspend) an endpoint.

### POST `/publishers/seren-db/projects/{id}/branches/{bid}/endpoints/{eid}/stop`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/projects/<id>/branches/<bid>/endpoints/<eid>/stop" \
  -H "Authorization: Bearer $SEREN_API_KEY"
```

## SQL Query

Execute SQL against a target project/branch/database using SerenDB credentials.

Execute a SQL query against a SerenDB database.

### POST `/publishers/seren-db/query`

```bash
curl -sS -X POST "https://api.serendb.com/publishers/seren-db/query" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<project_uuid>","query":"SELECT 1"}'
```
