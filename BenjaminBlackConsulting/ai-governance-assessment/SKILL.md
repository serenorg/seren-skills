---
name: ai-governance-assessment
description: "Assess AI governance maturity for compliance/risk leaders: evaluates 8 domains against NIST AI RMF, EU AI Act, ISO 42001, and OCC SR 11-7 frameworks. Persists results to SerenDB for tracking and follow-up."
---

# AI Governance Readiness Assessment

Assess your organization's AI governance maturity with results saved to SerenDB.

## What This Skill Provides

- Structured intake questionnaire for organization context
- Maturity scoring across 8 governance domains (1-5 scale)
- Framework alignment (NIST AI RMF, EU AI Act, ISO 42001, OCC SR 11-7)
- Gap analysis with prioritized recommendations
- Board-ready assessment reports
- **SerenDB persistence** for historical tracking and follow-up

## Runtime Files

- `scripts/serendb_schema.sql` - Database schema for assessment persistence

## When to Use

Activate this skill when the user asks about:
- "AI governance assessment"
- "assess our AI readiness"
- "AI compliance check"
- "governance maturity evaluation"
- "NIST AI RMF assessment"
- "EU AI Act readiness"
- "view past assessments"
- "track governance progress"

## SerenDB Setup (First Run)

Before running assessments, initialize the database schema via MCP:

1. **Resolve or create project:**
```
mcp__seren-mcp__list_projects
mcp__seren-mcp__create_project(name: "ai-governance")
```

2. **Create database:**
```
mcp__seren-mcp__create_database(project_id: "<project_id>", name: "governance_assessments")
```

3. **Apply schema:**
```
mcp__seren-mcp__run_sql(
  project_id: "<project_id>",
  database: "governance_assessments",
  query: "<contents of scripts/serendb_schema.sql>"
)
```

## Workflow

### Phase 1: Organization Context

Ask these intake questions:

1. **Industry**: Financial Services, Healthcare, Energy/Utilities, Government/Defense, Technology, Manufacturing, Other
2. **Size**: <100, 100-1000, 1000-10000, 10000+ employees
3. **AI Maturity**: Exploring (no production AI), Early (1-5 use cases), Scaling (5-20), Mature (20+)
4. **Regulatory Environment**: GDPR/EU AI Act, US Federal, Financial (OCC/SEC), Healthcare (HIPAA/FDA), Industry-specific
5. **Current State**: AI Center of Excellence, Ethics policy, Model inventory, Risk assessment process, Human oversight, Bias testing

### Phase 2: Framework Selection

Based on responses, determine applicable frameworks:

| Trigger | Framework |
|---------|-----------|
| EU operations | EU AI Act |
| US Federal/Government | NIST AI RMF |
| Financial services | OCC SR 11-7 |
| Healthcare | FDA AI/ML guidance |
| Any AI deployment | NIST AI RMF (baseline) |

### Phase 3: Domain Assessment

Evaluate maturity (1-5) across 8 domains:

1. **Strategy & Leadership** - Board oversight, AI governance body, executive sponsorship
2. **Risk Management** - AI risk inventory, assessment process, escalation procedures
3. **Data Governance** - Training data documentation, quality standards, consent management
4. **Model Lifecycle** - Versioning, approval workflows, drift detection, retirement
5. **Transparency** - Explainability, user disclosure, regulatory documentation
6. **Fairness & Bias** - Pre-deployment testing, protected attributes, remediation
7. **Security & Privacy** - AI-specific security, adversarial testing, privacy techniques
8. **Human Oversight** - High-risk review, override capability, operator training

**Scoring Guide:**
- 1: No governance, ad-hoc
- 2: Informal, reactive
- 3: Documented, emerging
- 4: Formal, proactive
- 5: Continuous, automated

### Phase 4: Gap Analysis

For each domain:
1. Calculate current score
2. Determine target based on risk profile
3. Identify gap (target - current)
4. Prioritize: CRITICAL (gap 3+, high urgency), HIGH, MEDIUM, LOW

### Phase 5: Save to SerenDB

After completing assessment, persist results via MCP:

**1. Insert main assessment record:**
```sql
INSERT INTO governance.assessments (
  organization_name, industry, employee_count, ai_maturity,
  overall_score, maturity_level, frameworks_applicable, assessor
) VALUES (
  'First Regional Bank', 'financial_services', '1000-10000', 'early',
  2.4, 'developing', ARRAY['occ_sr_11_7', 'nist_ai_rmf', 'fair_lending'], 'AI Governance Skill'
) RETURNING assessment_id;
```

**2. Insert domain scores:**
```sql
INSERT INTO governance.domain_scores (
  assessment_id, domain_name, current_score, target_score, gap, priority, notes
) VALUES
  ('<assessment_id>', 'strategy_leadership', 2.0, 4.0, 2.0, 'high', 'No board oversight'),
  ('<assessment_id>', 'risk_management', 2.0, 4.0, 2.0, 'critical', 'No model inventory'),
  -- ... all 8 domains
;
```

**3. Insert identified gaps:**
```sql
INSERT INTO governance.gaps (
  assessment_id, gap_title, domain_name, priority,
  current_state, target_state, risk_description,
  recommendation, effort, timeline_days
) VALUES (
  '<assessment_id>',
  'No AI/ML Model Inventory',
  'risk_management',
  'critical',
  '3 AI systems deployed with no centralized tracking',
  'Comprehensive inventory with risk classifications',
  'OCC examination deficiency, potential enforcement action',
  'Catalog all AI/ML systems using standardized template',
  'medium',
  30
);
```

**4. Insert risk register entries:**
```sql
INSERT INTO governance.risk_register (
  assessment_id, risk_id, category, description,
  likelihood, impact, risk_score, current_controls, recommended_actions
) VALUES (
  '<assessment_id>',
  'AI-001',
  'fairness',
  'Credit scoring model may have disparate impact',
  'high',
  'high',
  9,
  'None',
  'Immediate bias testing, ongoing monitoring'
);
```

**5. Insert roadmap items:**
```sql
INSERT INTO governance.roadmap_items (
  assessment_id, phase, phase_name, item_description, target_days
) VALUES
  ('<assessment_id>', 1, 'Foundation', 'Complete AI/ML model inventory', 30),
  ('<assessment_id>', 1, 'Foundation', 'Conduct bias testing on credit model', 60),
  ('<assessment_id>', 2, 'Build', 'Deploy model registry with approval workflows', 180);
```

### Phase 6: Generate Report

Output structured report (see `examples/sample-assessment-regional-bank.md`).

## Querying Past Assessments

**View all assessments:**
```sql
SELECT * FROM governance.v_assessment_summary ORDER BY assessment_date DESC;
```

**View specific assessment details:**
```sql
SELECT a.*, d.domain_name, d.current_score, d.target_score, d.priority
FROM governance.assessments a
JOIN governance.domain_scores d ON d.assessment_id = a.assessment_id
WHERE a.organization_name = 'First Regional Bank'
ORDER BY d.priority;
```

**Track gap remediation progress:**
```sql
SELECT * FROM governance.v_gap_progress
WHERE organization_name = 'First Regional Bank';
```

**Compare assessments over time:**
```sql
SELECT organization_name, assessment_date, overall_score, maturity_level,
       critical_gaps, high_gaps
FROM governance.v_assessment_summary
WHERE organization_name = 'First Regional Bank'
ORDER BY assessment_date;
```

**Update gap status:**
```sql
UPDATE governance.gaps
SET status = 'resolved', resolved_at = NOW(), resolution_notes = 'Model inventory completed'
WHERE assessment_id = '<id>' AND gap_title = 'No AI/ML Model Inventory';
```

## Example

**User**: "Assess our AI governance. We're a 2,000-employee regional bank with 3 AI systems."

**Agent workflow**:
1. Ask clarifying questions (AI maturity, regulations, current governance)
2. Determine frameworks (OCC SR 11-7, NIST AI RMF, Fair Lending)
3. Score each domain based on responses
4. Generate gap analysis with priorities
5. **Save results to SerenDB** via MCP SQL calls
6. Output board-ready report with roadmap
7. Provide assessment_id for future reference

**Follow-up**: "Show me our governance progress since last assessment"
- Query `governance.v_assessment_summary` for historical scores
- Query `governance.v_gap_progress` for remediation tracking

See `examples/sample-assessment-regional-bank.md` for full output.

## Target Users

- Chief Compliance Officers
- AI/ML Governance Program Managers
- Risk Leaders in Regulated Industries
- Enterprise Architects evaluating AI adoption

## About Benjamin Black Consulting

This skill is provided by Benjamin Black Consulting, specializing in AI strategy and governance advisory for regulated industries.

- AI Governance Frameworks
- Data Strategy & Architecture
- Compliance Readiness Assessments
- Executive AI Training
