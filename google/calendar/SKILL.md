---
name: google-calendar
description: "Create, read, update, and delete Google Calendar events. Supports free/busy queries, recurring events, multi-calendar management, and meeting scheduling with OAuth authentication."
---

# Google Calendar

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- create calendar event
- list calendar events
- check availability
- schedule meeting
- update calendar event

## Workflow Summary

1. `list_events` uses `connector.calendar.get`
2. `create_event` uses `connector.calendar.post`
3. `check_freebusy` uses `connector.calendar.post`
