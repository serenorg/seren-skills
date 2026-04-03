---
name: seren-scheduler
display-name: "Seren Scheduler"
description: "Create and manage scheduled HTTP jobs with the seren-cron publisher. Use when users want to create recurring webhook runs, list and inspect existing schedules, update jobs, pause or resume execution, organize jobs into groups, or review run history."
---

# Seren Scheduler

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- schedule a recurring webhook with seren
- create a cron job for an agent or API
- pause or resume a scheduled job
- inspect scheduled job run history

## Pricing

- This skill uses the `seren-cron` publisher.
- Current publisher listing: `$0.0001` per scheduled job execution.
- Job creation and management are lightweight control actions, but actual recurring executions spend from the user's SerenBucks balance.
- Before scaling a high-frequency schedule, confirm the live publisher listing in Seren because marketplace pricing can change.

## Paying With SerenBucks

- Seren skills and publisher calls are paid from the user's SerenBucks balance.
- Users can buy SerenBucks at `https://serendb.com/serenbucks` or through `https://console.serendb.com`.
- Current funding flow: add funds via Stripe, starting at `$5`.
- A verified email is required before making Stripe deposits through Seren.
- Current SerenBucks page also advertises free credits: `$1` signup bonus, `$5` payment-method bonus, and `$0.10/day` in daily claim credits.
- API-first users can fund their balance through `POST /wallet/deposit` on `https://api.serendb.com`.

## Workflow Summary

1. `health_check` uses `connector.scheduler.get`
2. `list_jobs` uses `connector.scheduler.get`
3. `get_job` uses `connector.scheduler.get`
4. `create_job` uses `connector.scheduler.post`
5. `update_job` uses `connector.scheduler.put`
6. `delete_job` uses `connector.scheduler.delete`
7. `pause_job` uses `connector.scheduler.post`
8. `resume_job` uses `connector.scheduler.post`
9. `get_results` uses `connector.scheduler.get`
10. `create_group` uses `connector.scheduler.post`
11. `list_groups` uses `connector.scheduler.get`
12. `update_group` uses `connector.scheduler.put`
13. `delete_group` uses `connector.scheduler.delete`
14. `summarize` uses `transform.scheduler_summary`
