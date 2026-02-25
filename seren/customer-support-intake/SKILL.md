---
name: customer-support-intake
description: "Secure customer support evidence collection for SerenDesktop incidents. Use when users report failures and need one-click collection of logs, diagnostics, chat history, and screenshots with consent checks, PII redaction, and storage in SerenDB."
---

# Customer Support Intake

## When to Use

- collect support logs for seren desktop
- package customer incident evidence
- capture screenshot and diagnostics for support

## Workflow Summary

1. `verify_consent` uses `transform.assert_consent`
2. `verify_support_org_db_access` uses `connector.storage.post`
3. `collect_environment` uses `connector.diagnostics.post`
4. `collect_logs` uses `connector.diagnostics.post`
5. `collect_chat_history` uses `connector.diagnostics.post`
6. `open_incident_view` uses `connector.playwright.post`
7. `capture_screenshot` uses `connector.playwright.post`
8. `redact_and_minimize` uses `transform.redact_sensitive`
9. `hash_payload` uses `transform.hash_payload`
10. `upsert_incident` uses `connector.storage.post`
11. `persist_evidence` uses `connector.storage.post`
12. `set_retention` uses `connector.storage.post`
13. `summary` uses `transform.support_summary`
