---
name: asana
description: "Manage Asana tasks, projects, sections, goals, portfolios, tags, teams, and workspaces through AI agents. Create, update, and organize work items with OAuth token passthrough."
---
# Asana

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- create asana task
- list asana projects
- update asana task status
- search asana tasks
- manage asana workspace

## Workflow Summary

1. `list_workspaces` uses `connector.asana.get`
2. `list_projects` uses `connector.asana.get`
3. `create_task` uses `connector.asana.post`
4. `search_tasks` uses `connector.asana.get`
