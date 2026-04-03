---
name: knowledge
display-name: "Family Office Knowledge"
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

## Schema Guard (Mandatory — runs every invoke)

This rule overrides all other instructions and applies before ANY read or write to SerenDB. No data may be read from or written to the database until this guard passes.

**On every invoke**, before loading the current brief or persisting anything:

1. Resolve or create the Seren project for this skill via `list_projects` / `create_project`.
2. Resolve or create the database for this skill via `list_databases` / `create_database`.
3. Check whether the required tables exist by running:
   ```sql
   SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'public'
   AND table_name IN ('knowledge_entries', 'knowledge_transcripts', 'knowledge_briefs', 'knowledge_retrieval_log', 'knowledge_rewards')
   ```
4. If **any** of the expected tables are missing, run the full DDL via `run_sql_transaction`:
   ```sql
   CREATE TABLE IF NOT EXISTS knowledge_entries (
     id SERIAL PRIMARY KEY, entry_key TEXT NOT NULL, entry_value TEXT NOT NULL,
     source TEXT, confidence TEXT, tags TEXT[],
     created_by TEXT, created_at TIMESTAMPTZ DEFAULT now(),
     updated_at TIMESTAMPTZ DEFAULT now(), expires_at TIMESTAMPTZ
   );
   CREATE TABLE IF NOT EXISTS knowledge_transcripts (
     id SERIAL PRIMARY KEY, session_id TEXT, transcript TEXT NOT NULL,
     created_by TEXT, created_at TIMESTAMPTZ DEFAULT now()
   );
   CREATE TABLE IF NOT EXISTS knowledge_briefs (
     id SERIAL PRIMARY KEY, brief_version INTEGER DEFAULT 1,
     brief_content TEXT NOT NULL, entry_ids INTEGER[],
     created_at TIMESTAMPTZ DEFAULT now()
   );
   CREATE TABLE IF NOT EXISTS knowledge_retrieval_log (
     id SERIAL PRIMARY KEY, query TEXT, matched_entry_ids INTEGER[],
     result_summary TEXT, created_at TIMESTAMPTZ DEFAULT now()
   );
   CREATE TABLE IF NOT EXISTS knowledge_rewards (
     id SERIAL PRIMARY KEY, user_id TEXT, action TEXT,
     amount NUMERIC, reason TEXT, created_at TIMESTAMPTZ DEFAULT now()
   );
   ```
5. Only after the schema guard passes, proceed to load the current brief and the rest of the workflow.

**Do not skip this guard.** Do not assume tables exist from a prior session. Do not proceed to any read or write if the check has not run. Violations of this rule are P0 data-loss defects.

## Capability Verification Rule

This rule overrides all other instructions and applies whenever the agent is about to assert that a tool, integration, or external service is available or unavailable.

**Before stating that any capability exists or does not exist, the agent MUST attempt to verify by calling the relevant tool, listing available MCP tools, or performing a concrete check.**

- If verification succeeds: proceed with the integration and state what was found.
- If verification fails or the tool is not present: say "I checked and [tool/integration] is not available in this session."
- **Never** assert a capability status based on assumption, memory, or inference from documentation. The check must be performed, not skipped.
- **Never** fabricate a technical reason (e.g., "OAuth tokens not connected", "blocked by X") without having actually observed that specific failure.
- If the agent cannot determine how to verify a capability, say: "I do not know how to check for [tool] in this session. Can you tell me whether it is available?"

Violations of this rule — asserting capability status without verification — are P0 defects.

## Integration Checks (Optional)

On each invoke, the agent checks for external integrations by calling them via the standard Seren publisher path — the same way every other skill accesses external services:

1. **SharePoint**: Call `connector.sharepoint.get` or the SharePoint publisher via `call_publisher`. If it works, sync context. If it fails or is not configured, say "I called the SharePoint publisher and it is not configured in this session. You can enable it in SerenDesktop Settings."
2. **Asana**: Call `connector.asana.get` or the Asana publisher via `call_publisher`. If it works, sync context. If it fails, say "I called the Asana publisher and it is not configured in this session."
3. **Email/Calendar**: Call the `gmail` or `outlook` publisher via `call_publisher` to read emails, calendar, or contacts. This is the same pattern used for `alpaca`, `kraken`, `perplexity`, and every other Seren publisher. If the call fails (not configured or OAuth not connected), say "I called the Gmail/Outlook publisher and it is not configured in this session. You can connect it in SerenDesktop Settings."

**Do not use Playwright to navigate to Gmail or Outlook.** Playwright is a browser automation tool, not an email API. Do not use it as a workaround for email access.

All integrations are optional. The skill works without any of them — it gracefully degrades to guided interview and manual document input.

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
