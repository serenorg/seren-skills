-- AI Governance Assessment persistence schema for SerenDB
-- Stores assessment results, domain scores, gaps, and recommendations.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS governance;

-- Main assessment record
CREATE TABLE IF NOT EXISTS governance.assessments (
  assessment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_name TEXT NOT NULL,
  industry TEXT NOT NULL,
  employee_count TEXT NOT NULL,
  ai_maturity TEXT NOT NULL CHECK (ai_maturity IN ('exploring', 'early', 'scaling', 'mature')),
  overall_score NUMERIC(3, 2) NOT NULL,
  maturity_level TEXT NOT NULL CHECK (maturity_level IN ('initial', 'emerging', 'developing', 'established', 'leading')),
  frameworks_applicable TEXT[] NOT NULL DEFAULT '{}',
  assessment_date DATE NOT NULL DEFAULT CURRENT_DATE,
  assessor TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assessments_org ON governance.assessments(organization_name);
CREATE INDEX IF NOT EXISTS idx_assessments_date ON governance.assessments(assessment_date DESC);
CREATE INDEX IF NOT EXISTS idx_assessments_industry ON governance.assessments(industry);

-- Domain scores for each assessment
CREATE TABLE IF NOT EXISTS governance.domain_scores (
  id BIGSERIAL PRIMARY KEY,
  assessment_id UUID NOT NULL REFERENCES governance.assessments(assessment_id) ON DELETE CASCADE,
  domain_name TEXT NOT NULL CHECK (domain_name IN (
    'strategy_leadership',
    'risk_management',
    'data_governance',
    'model_lifecycle',
    'transparency',
    'fairness_bias',
    'security_privacy',
    'human_oversight'
  )),
  current_score NUMERIC(3, 2) NOT NULL CHECK (current_score >= 1 AND current_score <= 5),
  target_score NUMERIC(3, 2) NOT NULL CHECK (target_score >= 1 AND target_score <= 5),
  gap NUMERIC(3, 2) NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('critical', 'high', 'medium', 'low')),
  notes TEXT,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (assessment_id, domain_name)
);

CREATE INDEX IF NOT EXISTS idx_domain_scores_assessment ON governance.domain_scores(assessment_id);
CREATE INDEX IF NOT EXISTS idx_domain_scores_priority ON governance.domain_scores(priority);

-- Framework alignment scores
CREATE TABLE IF NOT EXISTS governance.framework_alignment (
  id BIGSERIAL PRIMARY KEY,
  assessment_id UUID NOT NULL REFERENCES governance.assessments(assessment_id) ON DELETE CASCADE,
  framework_name TEXT NOT NULL,
  readiness_pct NUMERIC(5, 2) NOT NULL CHECK (readiness_pct >= 0 AND readiness_pct <= 100),
  requirements_met INTEGER NOT NULL DEFAULT 0,
  requirements_partial INTEGER NOT NULL DEFAULT 0,
  requirements_not_met INTEGER NOT NULL DEFAULT 0,
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (assessment_id, framework_name)
);

CREATE INDEX IF NOT EXISTS idx_framework_alignment_assessment ON governance.framework_alignment(assessment_id);

-- Identified gaps with recommendations
CREATE TABLE IF NOT EXISTS governance.gaps (
  id BIGSERIAL PRIMARY KEY,
  assessment_id UUID NOT NULL REFERENCES governance.assessments(assessment_id) ON DELETE CASCADE,
  gap_title TEXT NOT NULL,
  domain_name TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('critical', 'high', 'medium', 'low')),
  current_state TEXT NOT NULL,
  target_state TEXT NOT NULL,
  risk_description TEXT NOT NULL,
  recommendation TEXT NOT NULL,
  effort TEXT NOT NULL CHECK (effort IN ('low', 'medium', 'high')),
  timeline_days INTEGER NOT NULL,
  estimated_cost_low NUMERIC(12, 2),
  estimated_cost_high NUMERIC(12, 2),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved', 'accepted')),
  resolved_at TIMESTAMPTZ,
  resolution_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gaps_assessment ON governance.gaps(assessment_id);
CREATE INDEX IF NOT EXISTS idx_gaps_priority ON governance.gaps(priority);
CREATE INDEX IF NOT EXISTS idx_gaps_status ON governance.gaps(status);
CREATE INDEX IF NOT EXISTS idx_gaps_domain ON governance.gaps(domain_name);

-- Risk register entries
CREATE TABLE IF NOT EXISTS governance.risk_register (
  id BIGSERIAL PRIMARY KEY,
  assessment_id UUID NOT NULL REFERENCES governance.assessments(assessment_id) ON DELETE CASCADE,
  risk_id TEXT NOT NULL,
  category TEXT NOT NULL,
  description TEXT NOT NULL,
  likelihood TEXT NOT NULL CHECK (likelihood IN ('low', 'medium', 'high')),
  impact TEXT NOT NULL CHECK (impact IN ('low', 'medium', 'high')),
  risk_score INTEGER NOT NULL,
  current_controls TEXT,
  recommended_actions TEXT NOT NULL,
  owner TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'mitigating', 'accepted', 'closed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (assessment_id, risk_id)
);

CREATE INDEX IF NOT EXISTS idx_risk_register_assessment ON governance.risk_register(assessment_id);
CREATE INDEX IF NOT EXISTS idx_risk_register_status ON governance.risk_register(status);

-- Roadmap items
CREATE TABLE IF NOT EXISTS governance.roadmap_items (
  id BIGSERIAL PRIMARY KEY,
  assessment_id UUID NOT NULL REFERENCES governance.assessments(assessment_id) ON DELETE CASCADE,
  phase INTEGER NOT NULL CHECK (phase IN (1, 2, 3)),
  phase_name TEXT NOT NULL,
  item_description TEXT NOT NULL,
  target_days INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'blocked')),
  completed_at TIMESTAMPTZ,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_roadmap_items_assessment ON governance.roadmap_items(assessment_id);
CREATE INDEX IF NOT EXISTS idx_roadmap_items_phase ON governance.roadmap_items(phase);

-- Summary view for quick lookups
CREATE OR REPLACE VIEW governance.v_assessment_summary AS
SELECT
  a.assessment_id,
  a.organization_name,
  a.industry,
  a.ai_maturity,
  a.overall_score,
  a.maturity_level,
  a.assessment_date,
  a.frameworks_applicable,
  COUNT(DISTINCT g.id) FILTER (WHERE g.priority = 'critical') AS critical_gaps,
  COUNT(DISTINCT g.id) FILTER (WHERE g.priority = 'high') AS high_gaps,
  COUNT(DISTINCT g.id) FILTER (WHERE g.priority = 'medium') AS medium_gaps,
  COUNT(DISTINCT g.id) FILTER (WHERE g.priority = 'low') AS low_gaps,
  COUNT(DISTINCT r.id) FILTER (WHERE r.status = 'open') AS open_risks,
  COUNT(DISTINCT ri.id) FILTER (WHERE ri.status = 'completed') AS roadmap_completed,
  COUNT(DISTINCT ri.id) AS roadmap_total
FROM governance.assessments a
LEFT JOIN governance.gaps g ON g.assessment_id = a.assessment_id
LEFT JOIN governance.risk_register r ON r.assessment_id = a.assessment_id
LEFT JOIN governance.roadmap_items ri ON ri.assessment_id = a.assessment_id
GROUP BY a.assessment_id;

-- Progress tracking view
CREATE OR REPLACE VIEW governance.v_gap_progress AS
SELECT
  a.organization_name,
  a.assessment_id,
  g.gap_title,
  g.priority,
  g.status,
  g.timeline_days,
  g.created_at,
  EXTRACT(DAY FROM NOW() - g.created_at) AS days_open,
  CASE
    WHEN g.status = 'resolved' THEN 'on_track'
    WHEN EXTRACT(DAY FROM NOW() - g.created_at) > g.timeline_days THEN 'overdue'
    WHEN EXTRACT(DAY FROM NOW() - g.created_at) > g.timeline_days * 0.8 THEN 'at_risk'
    ELSE 'on_track'
  END AS tracking_status
FROM governance.gaps g
JOIN governance.assessments a ON a.assessment_id = g.assessment_id
WHERE g.status != 'resolved';
