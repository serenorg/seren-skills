-- pk-lead-intelligence SerenDB schema.
-- Populated incrementally across phases 1-4. The schema-guard step
-- applies this file inside an idempotent transaction on every invoke.

-- Phase 4: per-Lead enrichment recency ledger. Replaces the legacy
-- `Last_Enrichment_At__c` Salesforce custom field (issue #563). The
-- skill owns this state because the operator's Salesforce permission
-- set does not allow creating custom fields, and recency is purely
-- skill-internal — Salesforce does not need to know "when did the
-- skill last enrich this Lead?".
--
-- Keyed by Salesforce record id; one row per Lead. The cron's 24h
-- skip gate compares `now()` against `enriched_at` on every tick.
CREATE TABLE IF NOT EXISTS pk_lead_enrichment_log (
    lead_id        TEXT PRIMARY KEY,
    enriched_at    TIMESTAMPTZ NOT NULL,
    note_title     TEXT,
    agent_run_id   TEXT
);
