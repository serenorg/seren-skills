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

On each invoke, the agent checks for external integration availability:

1. **SharePoint**: Attempt to call `connector.sharepoint.get` or list relevant MCP tools. If available, sync context. If not, say "I checked and SharePoint integration is not connected in this session. You can enable it in SerenDesktop Settings."
2. **Asana**: Attempt to call `connector.asana.get` or list relevant MCP tools. If available, sync context. If not, say "I checked and Asana integration is not connected in this session."
3. **Email/Calendar**: Attempt to list available Gmail or Outlook MCP tools. If available, use them to enrich knowledge context. If not, say "I checked and email integration is not connected in this session. You can enable Gmail or Outlook in SerenDesktop Settings."

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
