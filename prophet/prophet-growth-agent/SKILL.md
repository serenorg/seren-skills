---
name: prophet-growth-agent
description: "Reinforce repeat Prophet market creation with lightweight status checks, progress tracking, reminder copy, and re-engagement recommendations after first success."
---

# Prophet Growth Agent

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- check Prophet growth agent status
- generate Prophet re-engagement reminders
- track progress toward repeated Prophet market creation
- run Prophet growth follow-up

## Workflow Summary

1. `normalize_request` uses `transform.normalize_request`
2. `connect_storage` uses `connector.storage.connect`
3. `load_recent_activity` uses `connector.storage.query`
4. `compute_progress` uses `transform.compute_repeat_creation_progress`
5. `generate_checkin_actions` uses `transform.generate_checkin_actions`
6. `compose_reminder_copy` uses `transform.compose_reminder_copy`
7. `persist_growth_outputs` uses `connector.storage.upsert`
8. `render_summary` uses `transform.render_report`
