-- BAT Sales Coach schema for SerenDB
-- Tables: prospects, behavior_tasks, behavior_journals, attitude_journals, technique_plans, coaching_sessions

CREATE SCHEMA IF NOT EXISTS {{schema_name}};

CREATE TABLE IF NOT EXISTS {{schema_name}}.prospects (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name            TEXT NOT NULL,
    organization    TEXT,
    pipeline_stage  TEXT DEFAULT 'new_lead',
    opportunity_value_usd NUMERIC(12,2) DEFAULT 0,
    expected_close_date TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.behavior_tasks (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_id      TEXT,
    prospect_id     TEXT REFERENCES {{schema_name}}.prospects(id),
    prospect_name   TEXT,
    organization    TEXT,
    pipeline_stage  TEXT,
    title           TEXT NOT NULL,
    behavior_type   TEXT DEFAULT 'outreach',
    status          TEXT DEFAULT 'planned',
    due_date        TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    prospect_response TEXT,
    next_behavior   TEXT,
    next_behavior_due TEXT,
    opportunity_value_usd NUMERIC(12,2) DEFAULT 0,
    expected_close_date TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.behavior_journals (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_id      TEXT,
    task_id         TEXT REFERENCES {{schema_name}}.behavior_tasks(id),
    planned_behavior TEXT,
    actual_behavior  TEXT,
    additional_wins  TEXT,
    prospect_response TEXT,
    next_behavior    TEXT,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.attitude_journals (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_id      TEXT,
    score           INTEGER CHECK (score BETWEEN 1 AND 10),
    body_signal     TEXT,
    future_statement TEXT,
    curiosity_state  TEXT DEFAULT 'unsure',
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.technique_plans (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_id      TEXT,
    technique_focus  TEXT,
    behavior_experiment TEXT,
    training_request TEXT,
    self_chosen_quota INTEGER DEFAULT 0,
    prospect_next_steps JSONB DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.coaching_sessions (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_date    TEXT,
    phase_reached   TEXT DEFAULT 'behavior',
    behaviors_planned INTEGER DEFAULT 0,
    behaviors_completed INTEGER DEFAULT 0,
    attitude_score  INTEGER,
    curiosity_gate_passed BOOLEAN DEFAULT false,
    summary         TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_behavior_tasks_status_due
    ON {{schema_name}}.behavior_tasks (status, due_date);

CREATE INDEX IF NOT EXISTS idx_behavior_tasks_prospect
    ON {{schema_name}}.behavior_tasks (prospect_id);

CREATE INDEX IF NOT EXISTS idx_coaching_sessions_date
    ON {{schema_name}}.coaching_sessions (session_date);
