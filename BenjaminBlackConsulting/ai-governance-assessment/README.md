# AI Governance Readiness Assessment

Extended documentation for the AI Governance Assessment skill.

## SerenDB Schema

Assessment results are persisted to SerenDB for historical tracking and remediation monitoring.

### Tables

| Table | Purpose |
|-------|---------|
| `governance.assessments` | Main assessment records with overall scores |
| `governance.domain_scores` | Individual domain maturity scores (8 per assessment) |
| `governance.framework_alignment` | Readiness percentages for each applicable framework |
| `governance.gaps` | Identified gaps with recommendations and status tracking |
| `governance.risk_register` | Risk entries with likelihood, impact, and controls |
| `governance.roadmap_items` | Phased remediation tasks with status tracking |

### Views

| View | Purpose |
|------|---------|
| `governance.v_assessment_summary` | Quick overview with gap counts and progress |
| `governance.v_gap_progress` | Track overdue and at-risk remediation items |

### Schema Setup

Apply the schema via MCP:

```
mcp__seren-mcp__run_sql(
  project_id: "<project_id>",
  database: "governance_assessments",
  query: "<contents of scripts/serendb_schema.sql>"
)
```

See `scripts/serendb_schema.sql` for full DDL.

## Domain Scoring Criteria

### Domain 1: Strategy & Leadership

| Score | Criteria |
|-------|----------|
| 1 | No AI strategy, ad-hoc decisions |
| 2 | Informal discussions, no formal governance |
| 3 | Documented strategy, emerging governance |
| 4 | Formal governance body, regular oversight |
| 5 | Board-level AI committee, integrated strategy |

**Key Questions:**
- Is AI governance a board-level priority?
- Is there an AI ethics committee or governance body?
- Are AI investments aligned with business strategy?
- Is there executive sponsorship for responsible AI?

### Domain 2: Risk Management

| Score | Criteria |
|-------|----------|
| 1 | AI risks not identified |
| 2 | Ad-hoc risk discussions |
| 3 | AI included in enterprise risk framework |
| 4 | Dedicated AI risk assessment process |
| 5 | Continuous AI risk monitoring, automated alerts |

**Key Questions:**
- Are AI systems cataloged in a risk inventory?
- Are AI-specific risks identified and assessed?
- Is there a process for AI risk escalation?
- Are risk tolerances defined for AI systems?

### Domain 3: Data Governance

| Score | Criteria |
|-------|----------|
| 1 | No data governance for AI |
| 2 | Basic data quality checks |
| 3 | Documented data lineage |
| 4 | Automated data quality monitoring |
| 5 | Full provenance, consent management, quality gates |

**Key Questions:**
- Is training data documented and traceable?
- Are data quality standards enforced?
- Is there consent management for AI training data?
- Are data retention policies applied to AI systems?

### Domain 4: Model Lifecycle Management

| Score | Criteria |
|-------|----------|
| 1 | Models deployed without documentation |
| 2 | Basic versioning, manual tracking |
| 3 | Model registry with metadata |
| 4 | Automated drift detection, approval workflows |
| 5 | Full MLOps with governance gates |

**Key Questions:**
- Are models versioned and documented?
- Is there a model approval process?
- Are models monitored for drift and degradation?
- Is there a process for model retirement?

### Domain 5: Transparency & Explainability

| Score | Criteria |
|-------|----------|
| 1 | Black box systems, no explainability |
| 2 | Technical documentation only |
| 3 | Business-readable explanations available |
| 4 | User-facing explanations, regulatory docs |
| 5 | Tiered explainability, automated disclosure |

**Key Questions:**
- Can AI decisions be explained to affected parties?
- Are AI systems disclosed to users/customers?
- Is there documentation for regulators?
- Are explainability requirements defined by risk tier?

### Domain 6: Fairness & Bias

| Score | Criteria |
|-------|----------|
| 1 | No bias considerations |
| 2 | Awareness but no testing |
| 3 | Pre-deployment bias testing |
| 4 | Continuous fairness monitoring |
| 5 | Automated bias detection, remediation workflows |

**Key Questions:**
- Is bias testing performed before deployment?
- Are protected attributes identified and monitored?
- Is there a process for bias remediation?
- Are fairness metrics defined and tracked?

### Domain 7: Security & Privacy

| Score | Criteria |
|-------|----------|
| 1 | AI excluded from security scope |
| 2 | Standard security controls only |
| 3 | AI-specific security requirements |
| 4 | Adversarial testing, privacy by design |
| 5 | Red teaming, differential privacy, secure enclaves |

**Key Questions:**
- Are AI systems included in security assessments?
- Is adversarial robustness tested?
- Are privacy-enhancing techniques used?
- Is AI-specific threat modeling performed?

### Domain 8: Human Oversight

| Score | Criteria |
|-------|----------|
| 1 | Fully autonomous, no oversight |
| 2 | Reactive oversight (post-hoc review) |
| 3 | Human-in-the-loop for high-risk |
| 4 | Defined oversight levels by risk tier |
| 5 | Continuous oversight, automated escalation |

**Key Questions:**
- Are high-risk decisions subject to human review?
- Can AI decisions be overridden?
- Are operators trained on AI limitations?
- Is there escalation for edge cases?

## Framework Reference

### NIST AI RMF Functions

| Function | Description |
|----------|-------------|
| GOVERN | Organizational governance structures, policies, accountability |
| MAP | Context and risk characterization, stakeholder identification |
| MEASURE | Risk and impact quantification, metrics and monitoring |
| MANAGE | Risk treatment, mitigation, and continuous improvement |

### EU AI Act Risk Categories

| Category | Description | Requirements |
|----------|-------------|--------------|
| Unacceptable Risk | Prohibited AI systems (social scoring, subliminal manipulation) | Banned |
| High Risk | Safety components, critical infrastructure, employment, credit | Conformity assessment, registration |
| Limited Risk | Chatbots, emotion recognition, deepfakes | Transparency obligations |
| Minimal Risk | Spam filters, video games, inventory management | No specific obligations |

### OCC SR 11-7 Model Risk Management

| Element | Requirement |
|---------|-------------|
| Model Development | Documentation of design, theory, and assumptions |
| Model Validation | Independent review and challenger models |
| Ongoing Monitoring | Performance tracking and validation updates |
| Governance | Board oversight, policies, and risk limits |
| Vendor Models | Same standards as internally developed |

### ISO 42001 AI Management System

| Clause | Focus Area |
|--------|------------|
| 4 | Context of the organization |
| 5 | Leadership and commitment |
| 6 | Planning (risks, objectives) |
| 7 | Support (resources, competence, awareness) |
| 8 | Operation (AI system lifecycle) |
| 9 | Performance evaluation |
| 10 | Improvement |

## Gap Priority Matrix

| Gap Size | Regulatory Urgency | Priority |
|----------|-------------------|----------|
| 3+ | High | CRITICAL |
| 2+ | High | HIGH |
| 3+ | Medium | HIGH |
| 2 | Medium | MEDIUM |
| 1 | Any | LOW |

## Maturity Levels

| Score Range | Level | Description |
|-------------|-------|-------------|
| 1.0 - 1.5 | Initial | Ad-hoc, reactive, no formal governance |
| 1.5 - 2.5 | Emerging | Awareness exists, informal processes |
| 2.5 - 3.5 | Developing | Documented policies, partial implementation |
| 3.5 - 4.5 | Established | Formal governance, proactive management |
| 4.5 - 5.0 | Leading | Continuous improvement, automated controls |

## Industry-Specific Considerations

### Financial Services
- OCC SR 11-7 model risk management
- Fair lending (ECOA, Regulation B)
- BSA/AML model validation
- SEC AI disclosure guidance

### Healthcare
- FDA AI/ML guidance for medical devices
- HIPAA privacy requirements
- Clinical decision support considerations
- Bias in diagnostic algorithms

### Government/Defense
- OMB M-24-10 AI guidance
- NIST AI RMF (mandatory for federal)
- Algorithmic accountability
- Procurement requirements

### Energy/Utilities
- Critical infrastructure protection
- NERC CIP compliance considerations
- Grid reliability AI governance
- Environmental impact considerations

## About Benjamin Black Consulting

Benjamin Black Consulting specializes in AI strategy and governance for regulated industries:

- **AI Governance Advisory**: Framework development, policy creation, board presentations
- **Data Strategy**: Architecture, governance models, quality management
- **Compliance Readiness**: NIST AI RMF, EU AI Act, OCC SR 11-7 alignment
- **Executive Coaching**: AI literacy, risk awareness, strategic decision-making

Contact: [Benjamin Black Consulting](https://www.linkedin.com/in/benjaminblackconsulting/)
