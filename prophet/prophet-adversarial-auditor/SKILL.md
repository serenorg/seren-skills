---
name: prophet-adversarial-auditor
description: "Inspect Prophet market creation history for rejected submissions, replayable failures, suspicious patterns, and plausible economic loss scenarios with structured findings for operators."
---

# Prophet Adversarial Auditor

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- audit Prophet market creation failures
- review Prophet rejected submissions
- inspect Prophet bugs and loss scenarios
- check Prophet auditor status

## Workflow Summary

1. `normalize_request` uses `transform.normalize_request`
2. `connect_storage` uses `connector.storage.connect`
3. `load_run_history` uses `connector.storage.query`
4. `replay_recent_runs` uses `transform.replay_recent_runs`
5. `detect_findings` uses `transform.detect_audit_findings`
6. `analyze_loss_scenarios` uses `transform.analyze_loss_hypotheses`
7. `rank_findings` uses `transform.rank_findings`
8. `persist_audit_outputs` uses `connector.storage.upsert`
9. `render_summary` uses `transform.render_report`
