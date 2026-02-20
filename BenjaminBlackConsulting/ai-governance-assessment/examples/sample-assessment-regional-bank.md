# AI Governance Readiness Assessment
## First Regional Bank
## Generated: February 20, 2026

---

## Executive Summary

**Overall Maturity Score: 2.4 / 5.0**
**Maturity Level: Developing**

First Regional Bank has made initial progress in AI governance but lacks formal structures required by OCC SR 11-7. Three AI systems (fraud detection, credit scoring, chatbot) operate without comprehensive governance. Immediate action is needed before the next regulatory examination.

### Key Findings
- **CRITICAL**: No AI/ML model inventory despite 3 production systems
- **CRITICAL**: Credit scoring model lacks required bias testing under ECOA
- **HIGH**: No board-level oversight of AI risks

### Immediate Actions Required
1. Establish AI model inventory within 30 days
2. Conduct bias testing on credit scoring model within 60 days
3. Create AI Risk Committee with board reporting within 90 days

---

## Maturity Scorecard

| Domain | Score | Target | Gap | Priority |
|--------|-------|--------|-----|----------|
| Strategy & Leadership | 2.0 | 4.0 | 2.0 | HIGH |
| Risk Management | 2.0 | 4.0 | 2.0 | CRITICAL |
| Data Governance | 3.5 | 4.0 | 0.5 | LOW |
| Model Lifecycle | 1.5 | 4.0 | 2.5 | CRITICAL |
| Transparency | 2.0 | 3.5 | 1.5 | MEDIUM |
| Fairness & Bias | 1.5 | 4.5 | 3.0 | CRITICAL |
| Security & Privacy | 3.0 | 4.0 | 1.0 | MEDIUM |
| Human Oversight | 2.5 | 4.0 | 1.5 | HIGH |

---

## Framework Alignment

### OCC SR 11-7 Model Risk Management
**Readiness: 35%**

| Requirement | Status | Gap |
|-------------|--------|-----|
| Model Inventory | NOT MET | No centralized inventory |
| Model Validation | PARTIAL | Credit model only |
| Ongoing Monitoring | NOT MET | No drift detection |
| Board Oversight | NOT MET | No regular reporting |
| Documentation | PARTIAL | Limited |

### Fair Lending (ECOA/Regulation B)
**Readiness: 25%**

| Requirement | Status | Gap |
|-------------|--------|-----|
| Disparate Impact Testing | NOT MET | No bias testing |
| Adverse Action Explanations | PARTIAL | Generic only |
| Fair Lending Documentation | NOT MET | No AI-specific analysis |

---

## Detailed Findings

### Critical Gaps

#### 1. No AI/ML Model Inventory
**Current State:** 3 AI systems deployed with no centralized tracking.
**Target State:** Comprehensive inventory with risk classifications and validation status.
**Risk:** OCC examination deficiency, potential enforcement action.
**Recommendation:** Catalog all AI/ML systems using standardized template within 30 days.
**Effort:** Medium
**Timeline:** 30 days

#### 2. Credit Scoring Model Lacks Bias Testing
**Current State:** AI credit model deployed 18 months ago without disparate impact testing.
**Target State:** Pre-deployment and ongoing bias testing against protected classes.
**Risk:** Fair lending violations, DOJ enforcement (recent settlements exceed $10M).
**Recommendation:** Engage third-party for immediate analysis, implement quarterly monitoring.
**Effort:** High
**Timeline:** 60 days

#### 3. No Model Lifecycle Management
**Current State:** Models deployed without approval workflows or drift detection.
**Target State:** MLOps with approval gates, version control, retirement criteria.
**Recommendation:** Implement model registry with governance workflows.
**Effort:** High
**Timeline:** 6 months

### High Priority Gaps

#### 4. No Board-Level AI Oversight
**Current State:** AI risks not reported to board. No AI Risk Committee.
**Target State:** Quarterly AI risk reporting, executive-sponsored governance body.
**Recommendation:** Add AI to board risk committee agenda, establish AI Risk Committee.
**Effort:** Medium
**Timeline:** 90 days

### Strengths

- Strong data governance foundation from BSA/AML compliance
- Existing third-party risk management framework
- Executive awareness and support for remediation

---

## Recommended Roadmap

### Phase 1: Foundation (0-3 months)
- [ ] Complete AI/ML model inventory (30 days)
- [ ] Conduct bias testing on credit model (60 days)
- [ ] Establish AI Risk Committee (90 days)
- [ ] Draft AI governance policy (60 days)

### Phase 2: Build (3-6 months)
- [ ] Deploy model registry with approval workflows
- [ ] Implement drift detection
- [ ] Develop AI risk assessment methodology
- [ ] Create model validation standards

### Phase 3: Scale (6-12 months)
- [ ] Automate fairness monitoring
- [ ] Integrate AI risk metrics into enterprise dashboard
- [ ] Develop explainability capabilities
- [ ] Prepare for regulatory examination

---

## Risk Register

| Risk ID | Category | Description | Likelihood | Impact | Actions |
|---------|----------|-------------|------------|--------|---------|
| AI-001 | Fairness | Credit model disparate impact | HIGH | HIGH | Immediate bias testing |
| AI-002 | Compliance | Model inventory gap | HIGH | HIGH | 30-day inventory |
| AI-003 | Operational | Chatbot incorrect info | MEDIUM | MEDIUM | Escalation rules |
| AI-004 | Model | Undetected drift | MEDIUM | HIGH | Automated monitoring |
| AI-005 | Governance | No board visibility | HIGH | MEDIUM | Formal reporting |

---

## Investment Estimate

| Initiative | One-Time | Annual | Timeline |
|------------|----------|--------|----------|
| Bias Testing (External) | $75,000 | $30,000 | 60 days |
| Model Registry | $25,000 | $40,000 | 6 months |
| AI Governance FTE | - | $180,000 | Ongoing |
| Board Training | $15,000 | $5,000 | 30 days |
| **Total** | **$115,000** | **$255,000** | - |

---

*Assessment generated using AI Governance Readiness Assessment skill*
*Provided by Benjamin Black Consulting*
