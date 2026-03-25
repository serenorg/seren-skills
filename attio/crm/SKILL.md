---
name: crm
description: "Connect to Attio CRM to manage contacts, companies, deals, lists, tasks, notes, meetings, and comments through AI agents. Search records, create entries, update pipelines, and manage your sales workflow."
---

# Attio

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- search attio contacts
- create attio record
- update attio deal
- list attio companies
- manage attio pipeline

## Workflow Summary

1. `list_objects` uses `connector.attio.get`
2. `search_records` uses `connector.attio.post`
3. `get_record` uses `connector.attio.get`
4. `create_record` uses `connector.attio.post`
5. `list_entries` uses `connector.attio.get`
