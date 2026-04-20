-- Family-office schema guard — idempotent DDL for all canonical object tables.
-- Applied by run_schema_guard() at the start of every skill invocation.
-- No multi-tenant client_id anywhere; single-family-office installation.
-- Foreign keys are soft TEXT for evolvability.

-- ===========================================================================
-- Support: audit_log — every audit_query call writes here.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    caller          TEXT NOT NULL,
    caller_role     TEXT NOT NULL,
    sql_hash        TEXT NOT NULL,
    param_count     INTEGER NOT NULL DEFAULT 0,
    row_count       INTEGER,
    duration_ms     INTEGER,
    error_class     TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===========================================================================
-- Support: execution_log — every executed / skipped / denied action.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS execution_log (
    id                SERIAL PRIMARY KEY,
    artifact_id       TEXT,
    skill_name        TEXT NOT NULL,
    action_id         TEXT NOT NULL,
    handler           TEXT NOT NULL,
    approval_status   TEXT NOT NULL,
    approved_by       TEXT,
    approved_at       TIMESTAMPTZ,
    executed_at       TIMESTAMPTZ,
    outcome           TEXT,
    outcome_detail    JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===========================================================================
-- Support: client_profile — deprecated singleton alias. Retained for
-- transitional compatibility with early tooling; new code writes to `office`.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS client_profile (
    id                        INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    family_name               TEXT,
    family_office_name        TEXT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===========================================================================
-- Canonical object families (16)
-- ===========================================================================

-- 1. office — singleton root
CREATE TABLE IF NOT EXISTS office (
    id                      TEXT PRIMARY KEY
                                CHECK (id = 'office:singleton'),
    object_type             TEXT NOT NULL DEFAULT 'office',
    lifecycle_state         TEXT NOT NULL DEFAULT 'active',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    principal_person_id     TEXT,
    coo_person_id           TEXT,
    family_office_name      TEXT NOT NULL,
    family_name             TEXT NOT NULL,
    cadence_schedule        JSONB,
    dms_config              JSONB,
    snowflake_config        JSONB,
    email_config            JSONB,
    calendar_config         JSONB,
    office_suite            TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. person — principal, spouse, dependents, beneficiaries, staff
CREATE TABLE IF NOT EXISTS person (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'person',
    lifecycle_state         TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    full_name               TEXT NOT NULL,
    role_in_family          TEXT,
    dob_year                INTEGER,
    residence_state         TEXT,
    contact                 JSONB,
    aliases                 JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. advisor — external CPA, attorney, custodian, broker, consultant
CREATE TABLE IF NOT EXISTS advisor (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'advisor',
    lifecycle_state         TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    full_name               TEXT NOT NULL,
    firm                    TEXT,
    role_type               TEXT,
    confidentiality_scope   TEXT[],
    contact                 JSONB,
    aliases                 JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. entity — trust, LLC, foundation, FLP
CREATE TABLE IF NOT EXISTS entity (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'entity',
    lifecycle_state         TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    legal_name              TEXT NOT NULL,
    entity_type             TEXT,
    jurisdiction            TEXT,
    ein                     TEXT,
    situs                   TEXT,
    primary_trustee_id      TEXT,
    aliases                 JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. account — custodial, bank, brokerage, trust
CREATE TABLE IF NOT EXISTS account (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'account',
    lifecycle_state         TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label   TEXT NOT NULL DEFAULT 'tax-sensitive',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    account_name            TEXT NOT NULL,
    institution             TEXT,
    account_type            TEXT,
    owning_entity_id        TEXT,
    external_id             TEXT,  -- sensitive; redacted in Snowflake payloads
    aliases                 JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6. asset — real estate, art, collectibles, private holdings
CREATE TABLE IF NOT EXISTS asset (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'asset',
    lifecycle_state         TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    asset_name              TEXT NOT NULL,
    asset_class             TEXT,
    owning_entity_id        TEXT,
    valuation_range         TEXT,
    location                TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 7. document — ingested source documents
CREATE TABLE IF NOT EXISTS document (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'document',
    lifecycle_state         TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    document_name           TEXT NOT NULL,
    document_kind           TEXT,
    retention_status        TEXT,
    effective_date          DATE,
    linked_entity_ids       TEXT[],
    linked_account_ids      TEXT[],
    dms_urls                JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 8. artifact — generated deliverables
CREATE TABLE IF NOT EXISTS artifact (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'artifact',
    lifecycle_state         TEXT NOT NULL DEFAULT 'active',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    pillar                  TEXT NOT NULL,
    skill_name              TEXT NOT NULL,
    artifact_name           TEXT NOT NULL,
    artifact_version        INTEGER,
    local_path              TEXT,
    dms_urls                JSONB,
    snowflake_row_id        TEXT,
    content_hash            TEXT,
    sink_status             JSONB,
    linked_entity_ids       TEXT[],
    linked_obligation_ids   TEXT[],
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 9. decision — recorded decisions with rationale
CREATE TABLE IF NOT EXISTS decision (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'decision',
    lifecycle_state         TEXT NOT NULL DEFAULT 'active',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    question                TEXT NOT NULL,
    options_considered      JSONB,
    chosen                  TEXT,
    rationale               TEXT,
    affected_object_ids     TEXT[],
    evidence_ids            TEXT[],
    approver_ids            TEXT[],
    decision_date           DATE,
    effective_date          DATE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 10. policy — versioned governance rules
CREATE TABLE IF NOT EXISTS policy (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'policy',
    lifecycle_state         TEXT NOT NULL DEFAULT 'active',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    policy_name             TEXT NOT NULL,
    scope                   TEXT,
    policy_version          INTEGER NOT NULL,
    text_human              TEXT,
    rules                   JSONB,
    precedence_rank         INTEGER NOT NULL DEFAULT 100,
    effective_date          DATE,
    retired_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 11. task — execution-oriented work items
CREATE TABLE IF NOT EXISTS task (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'task',
    lifecycle_state         TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    title                   TEXT NOT NULL,
    next_step               TEXT,
    due_date                DATE,
    review_date             DATE,
    depends_on_ids          TEXT[],
    obligation_id           TEXT,
    completion_evidence_id  TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 12. obligation — first-class deadlines / commitments
CREATE TABLE IF NOT EXISTS obligation (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'obligation',
    lifecycle_state         TEXT NOT NULL DEFAULT 'active',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT NOT NULL,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    title                   TEXT NOT NULL,
    description             TEXT,
    due_date                DATE,
    timing_window           TEXT,
    source_basis            TEXT NOT NULL,
    source_object_id        TEXT,
    priority                TEXT NOT NULL DEFAULT 'normal',
    linked_entity_ids       TEXT[],
    linked_account_ids      TEXT[],
    depends_on_ids          TEXT[],
    required_approvals      TEXT[],
    next_step               TEXT,
    last_update             TIMESTAMPTZ DEFAULT now(),
    completion_evidence_id  TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT obligation_has_owner CHECK (owner_id IS NOT NULL),
    CONSTRAINT obligation_has_source_basis CHECK (source_basis IS NOT NULL)
);

-- 13. approval — approval grants / denials; granted_at is immutable
CREATE TABLE IF NOT EXISTS approval (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'approval',
    lifecycle_state         TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    subject_object_id       TEXT NOT NULL,
    approver_id             TEXT NOT NULL,
    approval_type           TEXT NOT NULL,
    threshold_policy_id     TEXT,
    granted                 BOOLEAN,
    denied_reason           TEXT,
    granted_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Immutability trigger: once granted_at is set, it cannot be modified.
CREATE OR REPLACE FUNCTION approval_granted_at_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.granted_at IS NOT NULL AND NEW.granted_at IS DISTINCT FROM OLD.granted_at THEN
        RAISE EXCEPTION 'approval.granted_at is immutable once set';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS approval_granted_at_immutable_trg ON approval;
CREATE TRIGGER approval_granted_at_immutable_trg
    BEFORE UPDATE ON approval
    FOR EACH ROW EXECUTE FUNCTION approval_granted_at_immutable();

-- 14. communication — inbound/outbound correspondence
CREATE TABLE IF NOT EXISTS communication (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'communication',
    lifecycle_state         TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    direction               TEXT NOT NULL,
    channel                 TEXT NOT NULL,
    counterparty_advisor_id TEXT,
    subject                 TEXT,
    body_hash               TEXT,
    sent_at                 TIMESTAMPTZ,
    received_at             TIMESTAMPTZ,
    linked_matter_id        TEXT,
    review_item_id          TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 15. event — append-only audit stream. UPDATEs/DELETEs rejected by trigger.
CREATE TABLE IF NOT EXISTS event (
    id                      TEXT PRIMARY KEY,
    object_type             TEXT NOT NULL DEFAULT 'event',
    lifecycle_state         TEXT NOT NULL DEFAULT 'active',
    confidentiality_label   TEXT NOT NULL DEFAULT 'office-private',
    service_line            TEXT,
    owner_id                TEXT,
    source                  TEXT,
    source_ref              TEXT,
    provenance_status       TEXT NOT NULL DEFAULT 'asserted',
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at       TIMESTAMPTZ,
    superseded_by           TEXT,
    event_type              TEXT NOT NULL,
    actor_id                TEXT,
    subject_object_id       TEXT,
    summary                 TEXT,
    detail                  JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION event_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'event table is append-only; % rejected', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS event_no_update ON event;
CREATE TRIGGER event_no_update
    BEFORE UPDATE ON event
    FOR EACH ROW EXECUTE FUNCTION event_append_only();

DROP TRIGGER IF EXISTS event_no_delete ON event;
CREATE TRIGGER event_no_delete
    BEFORE DELETE ON event
    FOR EACH ROW EXECUTE FUNCTION event_append_only();

-- 16. review_item — human-oversight queue
CREATE TABLE IF NOT EXISTS review_item (
    id                        TEXT PRIMARY KEY,
    object_type               TEXT NOT NULL DEFAULT 'review_item',
    lifecycle_state           TEXT NOT NULL DEFAULT 'draft',
    confidentiality_label     TEXT NOT NULL DEFAULT 'office-private',
    service_line              TEXT,
    owner_id                  TEXT,
    source                    TEXT,
    source_ref                TEXT,
    provenance_status         TEXT NOT NULL DEFAULT 'asserted',
    captured_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at         TIMESTAMPTZ,
    superseded_by             TEXT,
    title                     TEXT NOT NULL,
    requested_action          TEXT NOT NULL,
    subject_object_id         TEXT NOT NULL,
    reviewer_role             TEXT NOT NULL,
    review_state              TEXT NOT NULL DEFAULT 'pending'
                                CHECK (review_state IN (
                                    'pending','approved','rejected',
                                    'returned','expired','executed'
                                )),
    evidence_ids              TEXT[],
    controlling_policy_id     TEXT,
    deadline                  TIMESTAMPTZ,
    not_approved_consequence  TEXT,
    resolution_note           TEXT,
    resolved_by_id            TEXT,
    resolved_at               TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===========================================================================
-- Indexes for hot queries
-- ===========================================================================
CREATE INDEX IF NOT EXISTS idx_obligation_due_active
    ON obligation (due_date) WHERE lifecycle_state = 'active';
CREATE INDEX IF NOT EXISTS idx_review_item_pending
    ON review_item (reviewer_role, deadline) WHERE review_state = 'pending';
CREATE INDEX IF NOT EXISTS idx_artifact_skill
    ON artifact (skill_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_subject
    ON event (subject_object_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_started
    ON audit_log (started_at DESC);
