---
name: knowledge
description: "Captures, stores, and retrieves institutional knowledge for family offices through guided knowledge interviews, SharePoint and document ingestion, Asana-aware context seeding, same-user cross-thread recall, explicit freshness cues, and retrieval-linked SerenBucks incentives."
---

# Knowledge

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- capture knowledge for the office
- start a knowledge session
- knowledge office
- what did i say about
- show me the current working brief
- what changed since the first brief

## Workflow Summary

1. `normalize_request` uses `transform.normalize_request`
2. `load_current_brief` uses `connector.storage.query`
3. `sync_sharepoint_context` uses `connector.sharepoint.get`
4. `sync_asana_context` uses `connector.asana.get`
5. `extract_document_text` uses `connector.docreader.post`
6. `conduct_guided_interview` uses `transform.run_guided_interview`
7. `distill_knowledge_entries` uses `transform.distill_knowledge_entries`
8. `archive_transcript` uses `connector.storage.upsert`
9. `persist_knowledge_entries` uses `connector.storage.upsert`
10. `retrieve_candidate_entries` uses `connector.storage.query`
11. `apply_access_and_freshness_rules` uses `transform.apply_access_and_freshness_rules`
12. `compose_answer_or_followup` uses `transform.compose_answer_or_followup`
13. `log_retrieval_events` uses `connector.storage.upsert`
14. `calculate_rewards` uses `transform.calculate_rewards`
15. `persist_rewards` uses `connector.storage.upsert`
16. `render_working_brief` uses `transform.render_working_brief`
