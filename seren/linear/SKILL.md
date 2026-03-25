---
name: linear
description: "Connect to Linear to manage issues, projects, cycles, and team workflows through AI agents. Create and update issues, manage project roadmaps, track sprint cycles, and search across your workspace."
---

# Linear

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- create linear issue
- list linear issues
- update linear issue
- search linear projects
- manage linear cycle

## Workflow Summary

1. `list_teams` uses `connector.linear.get`
2. `list_issues` uses `connector.linear.get`
3. `create_issue` uses `connector.linear.post`
4. `update_issue` uses `connector.linear.patch`
5. `list_projects` uses `connector.linear.get`
