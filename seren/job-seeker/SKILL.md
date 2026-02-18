---
name: job-seeker
description: "AI-powered job search agent that finds hiring managers, researches companies, discovers networking events, generates personalized outreach, and auto-applies to jobs — dual strategy for maximum coverage"
---

# Job Seeker & Apply

AI-powered job search agent following Taariq Lewis's proven strategy: **network with hiring managers directly, skip the ATS black hole.**

## ⚠️ IMPORTANT DISCLAIMERS

**READ THIS BEFORE USING**

### Ethical Use Only
- This skill is for **legitimate job seeking and professional networking**
- **Respect privacy**: Only contact people with professional context
- **Follow platform ToS**: Don't scrape data in violation of terms of service
- **No spam**: Personalized outreach only, never mass unsolicited emails
- **Respect opt-outs**: Honor requests to stop contact immediately

### Data Privacy
- Contact data (emails, phone numbers) is **personal information**
- Handle responsibly and delete when no longer needed
- Comply with **GDPR, CAN-SPAM, and local data protection laws**
- Never sell or share contact information

### No Guarantees
- This skill **does not guarantee job offers**
- Success depends on your skills, market conditions, and approach
- Networking takes time — expect weeks to months, not days

### Publisher Costs
- **Apollo.io**: $0.04 per contact lookup (verified emails)
- **AlphaGrowth**: $0.03 per company discovery + $0.01 per email verification
- **Perplexity + Exa**: $0.22 per company research
- **Playwright**: $0.04 per event discovery
- **Seren Models (GPT-5.2)**: $3.00 per outreach generation
- **Total**: $11-18 per comprehensive search (50 companies → 10 targets → 3 outreach emails)

---

## When to Use This Skill

Activate this skill when the user mentions:
- "help me find a job"
- "search for [role] positions at [companies]"
- "find hiring managers at [company]"
- "generate personalized outreach emails"
- "discover networking events in [location/industry]"

## For Claude: How to Invoke This Skill

When the user asks to **find jobs** or **apply to companies**, follow this 7-phase workflow:

### Phase 0: User Profile Extraction
**Goal**: Extract user's background from resume + LinkedIn data export BEFORE searching for jobs

**CRITICAL: Run this FIRST. Without user context, we can't filter companies, tailor outreach, or auto-fill applications.**

**BOTH resume AND LinkedIn export are REQUIRED.**

**Step 1: User downloads LinkedIn data export (REQUIRED)**

**How to download LinkedIn data export:**
1. Go to [linkedin.com/mypreferences/d/download-my-data](https://linkedin.com/mypreferences/d/download-my-data)
2. Select "Download larger data archive" → Check all data types
3. Click "Request archive" (takes 10-15 minutes)
4. LinkedIn emails download link
5. Download ZIP file (e.g., `Basic_LinkedInDataExport.zip`)

```bash
python3 scripts/agent.py extract-profile \
  --resume resume.pdf \
  --linkedin-export linkedin-export.zip \
  --output user_profile.json
```

**What gets extracted:**

**From Resume:**
- **Work history**: Companies, roles, duration, achievements
- **Skills & tech stack**: Languages, frameworks, tools
- **Education**: Degrees, schools, years
- **Notable achievements**: Quantified impact

**From LinkedIn Export (REQUIRED):**
- **Connections**: Mutual connections at target companies
- **Recommendations**: Who endorsed your skills
- **Activity**: Recent posts, comments (shows current interests)
- **Complete work history**: Including descriptions LinkedIn has
- **Skills endorsements**: Validation from colleagues

**Tools used:**
- **GPT-5.2**: Parse resume PDF/DOCX + LinkedIn CSV/JSON → structured JSON
- **No scraping**: User provides data directly (ToS-compliant)

**Cost**: $0.50 per user (resume + LinkedIn parsing)

**Present to user:**
```text
Profile extracted!

Alex Chen - Senior ML Engineer
• 8 years experience
• Skills: Python, Rust, PyTorch, distributed training
• Location: SF, NYC, or Remote
• Salary: $180k+ minimum
• Top achievement: Reduced training time 40%
• LinkedIn connections: 487 (23 at target companies)

Ready to find companies matching your profile?
```

---

### Phase 1: Company Discovery
**Goal**: Find 50 target companies matching user criteria (using Phase 0 profile)

**Uses Phase 0 profile to filter:**
- Match seniority (don't suggest junior roles to seniors)
- Match tech stack (PyTorch experience → ML companies)
- Match location preferences (remote, SF, NYC)
- Match industry experience (fintech background → fintech startups)

```bash
# Discover companies via AlphaGrowth
# Example: Find 50 AI startups in SF with 10-100 employees
python3 scripts/agent.py discover \
  --profile user_profile.json \
  --role "Senior ML Engineer" \
  --industry "AI" \
  --location "SF" \
  --limit 50
```

**What this does:**
- Searches AlphaGrowth database for companies matching filters
- Returns: company name, domain, size, funding, location
- Cost: $0.03 per company discovered (~$1.50 for 50 companies)

**Present to user:**
```text
Found 50 companies matching your criteria:

Top 10:
1. Anthropic (anthropic.com) - 150 employees, Series C, SF
2. Runway ML (runwayml.com) - 85 employees, Series B, NYC
3. Cohere (cohere.ai) - 120 employees, Series C, Toronto
...
```

---

### Phase 2: Company Research
**Goal**: Deep research on top 20 companies (culture, tech stack, recent news, hiring signals)

```bash
# For each company:
# 1. Perplexity: "Research [company] culture, tech stack, recent news, and hiring signals"
# 2. Exa: Semantic search for engineering blogs, job postings, employee interviews
```

**What this does:**
- Uses Perplexity to research company background, culture, recent developments
- Uses Exa to find technical content (engineering blogs, talks, open source)
- Identifies hiring signals (recent funding, team growth, new products)
- Cost: $0.22 per company (~$4.40 for 20 companies)

**Present to user:**
```text
Researched 20 companies. Top insights:

Anthropic:
  • Culture: Research-focused, safety-first, academic feel
  • Tech: Rust, Python, PyTorch, distributed systems
  • Recent: Just launched Claude 4.6, hiring ML engineers
  • Signals: 3 job postings this week, 15% team growth
```

---

### Phase 3: Hiring Manager Discovery (Apollo Primary)
**Goal**: Find decision-makers (hiring managers, team leads, VPs of Engineering)

**Primary Tool: Apollo.io**
```bash
# Apollo people search with filters:
# - Company: [target company domain]
# - Titles: "Engineering Manager", "VP Engineering", "Director of Engineering", "Head of ML"
# - Seniority: Manager, Director, VP, C-Suite
# Returns: Name, title, verified email, LinkedIn, phone (optional)
```

**Why Apollo is better:**
- ✅ **Verified emails** (90%+ accuracy) vs scraped emails
- ✅ **Job change tracking** — alerts when people switch companies
- ✅ **Hiring signals** — shows when companies are actively hiring
- ✅ **ToS-compliant** — Apollo provides data legally
- ✅ **Cost-effective** — $0.04 per contact vs $0.25/company for Apify scraping

**Secondary Tool: Playwright (LinkedIn context)**
```bash
# For top 3-5 targets per company:
# 1. Visit LinkedIn profile (public data only)
# 2. Extract: Recent posts, conference talks, interests, mutual connections
# 3. Gather social context for personalization
```

**What this does:**
- Apollo returns 5-10 hiring managers per company with verified emails
- Playwright enriches top candidates with social context (posts, talks, interests)
- Identifies warm intro opportunities (mutual connections)
- Cost: $4.00 per company batch (~$40 for 10 companies)

**Present to user:**
```text
Found 47 hiring managers across 10 companies:

Anthropic (5 contacts):
  1. Sarah Chen - VP Engineering
     • Email: sarah.chen@anthropic.com (verified ✓)
     • LinkedIn: Recent post about scaling ML training
     • Context: Spoke at MLSys 2026, interested in distributed systems

  2. Michael Rodriguez - Engineering Manager, Safety Team
     • Email: m.rodriguez@anthropic.com (verified ✓)
     • Mutual: 2 connections (Jane Doe, John Smith)
```

---

### Phase 4: Event Discovery
**Goal**: Find networking events, conferences, meetups where hiring managers will be

```bash
# Exa semantic search:
# "AI conferences in San Francisco March 2026"
# "Engineering meetups at Anthropic headquarters"

# Playwright scraping:
# - Eventbrite: AI/ML events
# - Meetup.com: Tech meetups
# - Luma: Startup events
# - LinkedIn Events: Company-hosted events
```

**What this does:**
- Discovers upcoming events in target location/industry
- Identifies which hiring managers are attending or speaking
- Provides context for in-person networking opportunities
- Cost: $0.04 per event discovery (~$0.40 for 10 events)

**Present to user:**
```text
Found 8 networking opportunities:

1. AI Safety Summit - March 15, 2026 - SF
   • Speakers: Sarah Chen (Anthropic), 3 other targets
   • Registration: $50 early bird

2. Bay Area ML Meetup - March 22, 2026 - SF
   • Host: Anthropic (office tour + tech talk)
   • Free, RSVP required
```

---

### Phase 5: Email Verification & Personalized Outreach
**Goal**: Verify emails, generate personalized outreach that demonstrates value

**Email Verification (AlphaGrowth)**
```bash
# For each contact email from Apollo:
# AlphaGrowth email verification API
# Returns: deliverable, risky, or invalid
# Only proceed with "deliverable" emails
```

**Why verify emails:**
- ✅ **Reduces bounce rate** — avoid damaging sender reputation
- ✅ **Improves deliverability** — ISPs trust senders with low bounce rates
- ✅ **Saves costs** — don't waste outreach on dead emails
- ✅ **Professional** — bounced emails look careless
- **Cost**: $0.01 per email verified (~$0.50 for 50 contacts)

**Outreach Generation (Seren Models - GPT-5.2)**
```bash
# For each verified contact, generate personalized email:
# Input:
# - Hiring manager name, title, company
# - Company research (culture, tech stack, recent news)
# - Social context (recent posts, talks, interests)
# - Mutual connections
# - User's background and value proposition
# - Event opportunity (if applicable)
# - **Application ID from Phase 7** (if using double-tap strategy)

# Output: 3-paragraph email
# 1. Personal hook (reference their work, post, or talk)
# 2. Application reference (if double-tap) + Value proposition
# 3. Soft ask (coffee, event meetup, or intro call)
```

**Outreach Principles (from Taariq's blog):**
- **Quality over quantity** — 3 great emails > 30 generic ones
- **Value-first** — lead with what you can offer, not what you need
- **Research-backed** — reference specific details (posts, projects, tech)
- **No resume dumps** — skip "attached is my resume", focus on conversation
- **Event-based** — "I'll be at AI Safety Summit, would love to chat" (warmer than cold email)
- **Double-tap** — Reference your ATS application to make it actionable

**Cost**: $3.00 per outreach email (~$9.00 for 3 high-priority emails)

**Present to user:**
```text
Email verification complete:
  • 47 emails checked
  • 43 deliverable (91%)
  • 3 risky (skipped)
  • 1 invalid (skipped)

Generated 3 personalized emails for top targets:

---

Subject: Your MLSys talk on distributed training + my application (#AN-2026-00142)

Hi Sarah,

I just applied for the Senior ML Engineer - Safety Team role (Application
#AN-2026-00142) and wanted to reach out directly.

I caught your MLSys 2026 talk on scaling distributed training to 10k GPUs — the
part about gradient compression was brilliant. I've been working on similar
challenges at [Current Company], where we reduced training time 40% by
implementing a custom allreduce algorithm.

I'm excited about Anthropic's approach to safety-constrained training at scale
and would love to learn more. I'll be at the AI Safety Summit on March 15 —
any chance you're free for coffee that morning?

Looking forward to connecting,
[Your Name]

---
```

---

### Phase 6: Application Tracking
**Goal**: Track outreach, responses, interviews, and follow-ups

```bash
# Store in structured format (SQLite or Google Sheets):
# - Company name
# - Contact name, title, email
# - Outreach sent date
# - Response received (yes/no)
# - Interview scheduled (yes/no)
# - Status (pending, interview, offer, rejected)
# - Follow-up date
# - Notes
```

**What this does:**
- Maintains CRM-style database of all outreach
- Tracks response rates and conversion funnel
- Schedules follow-ups (if no response in 7 days)
- Measures effectiveness (which companies/approaches work best)

**Present to user:**
```text
Application Tracker Summary:

Outreach sent: 43 emails
Responses: 8 (18.6%)
Interviews scheduled: 3 (7.0%)
Offers: 0
Pending follow-ups: 12 (due this week)

Top performers:
  • Event-based outreach: 4/10 responses (40%)
  • Mutual connection intro: 2/5 responses (40%)
  • Cold email (research-backed): 2/28 responses (7%)
```

---

### Phase 7: Automated Job Applications (Double-Tap Strategy)
**Goal**: Apply via ATS to the SAME 10 companies where you're networking, then reference your application in hiring manager outreach

**CRITICAL: Phase 7 operates on the SAME companies from Phase 3, not a separate list.**

**The Double-Tap Strategy:**
1. Apply via ATS (Phase 7) → Get confirmation number
2. Email hiring manager (Phase 5) → Reference application ID
3. Result: Your application doesn't go to a black hole + hiring manager knows to look for it

**Why this works:**
- ✅ **Double visibility**: HR sees application, hiring manager sees email
- ✅ **Shows initiative**: Applied formally AND reached out directly
- ✅ **Actionable**: Hiring manager can pull your application by ID
- ✅ **Higher conversion**: 10-15% (vs 2-5% ATS alone, 7-40% networking alone)

**Example flow for Anthropic:**
```bash
# 1. Apply via ATS (Phase 7)
→ Submit application
→ Get confirmation: "Application #AN-2026-00142"

# 2. Email Sarah Chen (Phase 5)
→ Subject: "Your MLSys talk + my application (#AN-2026-00142)"
→ Body: "I just applied for Senior ML Engineer (Application #AN-2026-00142)
         and wanted to reach out directly. I caught your MLSys talk..."
```

**Workflow per company:**
```bash
# For each of the 10 target companies:
# 1. Scrape careers page for matching job postings
# 2. Apply via ATS (auto-fill, submit, get confirmation ID)
# 3. Generate networking email that references application ID
# 4. User sends networking email
```

**What this does:**
- **Job discovery**: Playwright scrapes company careers pages
  - Supports: Greenhouse, Lever, Workday, Ashby, custom ATS
  - Filters by role keywords, location, seniority
- **Resume tailoring**: GPT-5.2 generates role-specific resume variants
  - Highlights relevant experience for each position
  - Optimizes for ATS keyword matching
- **Cover letter generation**: Personalized per role
  - References company research from Phase 2
  - Explains fit for specific position
- **Form automation**: Playwright fills forms automatically
  - Personal info (name, email, phone, LinkedIn)
  - Work history (auto-populated from master resume)
  - Education, skills, portfolio links
  - Handles dropdowns, checkboxes, text fields
- **Document upload**: Attaches PDF resume + cover letter
- **CAPTCHA solving**: Uses 2Captcha for bot detection bypass
- **Submission**: Clicks submit, saves confirmation

**Supported ATS platforms:**
- ✅ Greenhouse (60% of startups)
- ✅ Lever (25% of startups)
- ✅ Workday (enterprise companies)
- ✅ Ashby (newer startups)
- ✅ Custom forms (best-effort)

**Cost**: $2-5 per company
- Job discovery: $0.10 (scrape careers page)
- Resume tailoring: $0.50 per variant (GPT-5.2)
- Cover letter: $0.50 per role (GPT-5.2)
- Form automation: $1.00 per application (Playwright)
- CAPTCHA solving: $0.50 per captcha (2Captcha)

**Present to user:**
```text
Found 12 job postings across 10 companies:

Anthropic (2 openings):
  1. Senior ML Engineer - Safety Team
     • Location: SF (Hybrid)
     • Salary: $180k-250k
     • Applied: ✓ (submitted 2026-03-10 14:35 UTC)
     • Confirmation: Application #AN-2026-00142

  2. Staff Engineer - Infrastructure
     • Location: Remote (US)
     • Salary: $200k-280k
     • Applied: ✓ (submitted 2026-03-10 14:38 UTC)
     • Confirmation: Application #AN-2026-00143

Runway ML (1 opening):
  1. Senior Software Engineer - ML Platform
     • Location: NYC (Onsite)
     • Salary: $160k-220k
     • Applied: ✓ (submitted 2026-03-10 14:42 UTC)
     • Confirmation: Application #RML-2026-00089

Summary:
  • Jobs found: 12
  • Applied: 12 (100%)
  • Failed: 0
  • Total cost: $36.00 (12 applications × $3.00 avg)
  • Time saved: ~6 hours (vs manual application)
```

**Best practices:**
- **Apply selectively**: Don't spam every posting
- **Tailor per role**: Generic applications have <2% success
- **Network in parallel**: ATS alone has 2-5% conversion, networking + ATS = 10-15%
- **Follow up**: Email hiring manager after applying (reference application ID)

**Ethical considerations:**
- ✅ Auto-fill forms with accurate info
- ✅ Generate honest, tailored cover letters
- ✅ Only apply to roles you're qualified for
- ❌ Don't lie about experience or skills
- ❌ Don't mass-apply to every posting
- ❌ Don't bypass "no bots" ToS without permission

---

## How to Run a Complete Job Search

### Prerequisites

1. **User provides:**
   - Target role (e.g., "Senior ML Engineer")
   - Industries/companies (e.g., "AI startups, series A-C, 50-200 employees")
   - Location (e.g., "San Francisco Bay Area, NYC, remote")
   - Dealbreakers (e.g., "Must have GPU budget, remote-first culture")

2. **Budget:**
   - Minimum: $20 SerenBucks (covers 1-2 comprehensive searches)
   - Recommended: $50 SerenBucks (covers 3-5 searches with multiple iterations)

### Full Workflow Example

**User says:** "Help me find Senior ML Engineer roles at AI startups in San Francisco"

**Phase 1: Company Discovery**
```bash
python3 scripts/agent.py discover \
  --role "Senior ML Engineer" \
  --industry "Artificial Intelligence" \
  --location "San Francisco" \
  --employee_range "50-200" \
  --funding_stage "Series A,Series B,Series C" \
  --limit 50
```

**Phase 2: Research Top 20**
```bash
python3 scripts/agent.py research \
  --companies companies.json \
  --limit 20 \
  --output research.json
```

**Phase 3: Find Hiring Managers (Apollo)**
```bash
python3 scripts/agent.py find-contacts \
  --companies research.json \
  --titles "Engineering Manager,VP Engineering,Director of Engineering" \
  --tool apollo \
  --output contacts.json
```

**Phase 3b: Enrich with Social Context (Playwright)**
```bash
python3 scripts/agent.py enrich-contacts \
  --contacts contacts.json \
  --tool playwright \
  --limit 10 \
  --output contacts_enriched.json
```

**Phase 4: Discover Events**
```bash
python3 scripts/agent.py discover-events \
  --location "San Francisco" \
  --industry "AI,Machine Learning" \
  --date_range "2026-03-01,2026-04-30" \
  --output events.json
```

**Phase 5a: Verify Emails**
```bash
python3 scripts/agent.py verify-emails \
  --contacts contacts_enriched.json \
  --output contacts_verified.json
```

**Phase 5b: Generate Outreach**
```bash
python3 scripts/agent.py generate-outreach \
  --contacts contacts_verified.json \
  --background user_background.txt \
  --events events.json \
  --limit 3 \
  --output outreach.json
```

**Phase 6: Track Applications**
```bash
python3 scripts/agent.py track \
  --outreach outreach.json \
  --database applications.db
```

**Phase 7: Automated Job Applications**
```bash
python3 scripts/agent.py auto-apply \
  --companies research.json \
  --role "Senior ML Engineer" \
  --resume resume.pdf \
  --background user_background.txt \
  --limit 12 \
  --output applications.json
```

---

## Cost Breakdown

### Networking-Only Strategy (50 companies → 10 targets → 3 outreach)

| Phase | Tool | Cost |
|-------|------|------|
| 1. Company Discovery | AlphaGrowth | $1.50 (50 companies × $0.03) |
| 2. Company Research | Perplexity + Exa | $4.40 (20 companies × $0.22) |
| 3. Hiring Manager Discovery | Apollo | $4.00 (10 companies × $0.04 × 10 contacts) |
| 3b. Social Context Enrichment | Playwright | $0.40 (10 top targets × $0.04) |
| 4. Event Discovery | Exa + Playwright | $0.40 (10 events × $0.04) |
| 5a. Email Verification | AlphaGrowth | $0.50 (50 emails × $0.01) |
| 5b. Outreach Generation | Seren Models (GPT-5.2) | $9.00 (3 emails × $3.00) |
| **Total (Networking Only)** | | **$20.20** |

### Full Strategy (Networking + ATS Applications)

| Phase | Tool | Cost |
|-------|------|------|
| 1-5b. Networking (from above) | Multiple | $20.20 |
| 7. Automated Job Applications | Playwright + GPT-5.2 | $36.00 (12 applications × $3.00) |
| **Total (Networking + ATS)** | | **$56.20** |

### Strategy Comparison

| Approach | Cost | Volume | Conversion | Expected Interviews |
|----------|------|--------|------------|---------------------|
| **Networking only** | $20.20 | 3 outreach | 7-40% | 0.2-1.2 |
| **ATS only** | $36.00 | 12 applications | 2-5% | 0.24-0.6 |
| **Combined** | $56.20 | 3 outreach + 12 apps | Blended | 0.44-1.8 |

**Recommendation**: Use combined strategy for maximum coverage. Networking gets you high-quality conversations, ATS gets you volume/backup.

**Optimized for budget:**
- Research 10 companies instead of 20: -$2.20
- Generate 2 outreach emails instead of 3: -$3.00
- Skip Playwright social enrichment: -$0.40
- **Budget total: $14.60**

**Minimum viable:**
- Research 5 companies: $1.10
- Find contacts for 3 companies: $1.20
- Generate 1 outreach email: $3.00
- **Minimum total: $5.30**

---

## Implementation Status

### ✅ Fully Implemented & Working

**Phase 1: Company Discovery**
- ✅ AlphaGrowth company search with filters
- ✅ Export to JSON for next phase

**Phase 2: Company Research**
- ✅ Perplexity research integration
- ✅ Exa semantic search for technical content
- ✅ Hiring signal detection

**Phase 3: Hiring Manager Discovery**
- ✅ Apollo.io people search (verified emails)
- ✅ Playwright LinkedIn enrichment (social context)
- ✅ Mutual connection detection

**Phase 4: Event Discovery**
- ✅ Exa event search
- ✅ Playwright scraping (Eventbrite, Meetup, Luma)
- ✅ Speaker/attendee matching

**Phase 5: Email Verification & Outreach**
- ✅ AlphaGrowth email verification
- ✅ GPT-5.2 personalized email generation
- ✅ Template system with user background injection

**Phase 6: Application Tracking**
- ✅ SQLite database schema
- ✅ CRM-style tracking (outreach, responses, interviews)
- ✅ Follow-up scheduling

**Phase 7: Automated Job Applications**
- ✅ Playwright job posting scraper (Greenhouse, Lever, Workday, Ashby)
- ✅ GPT-5.2 resume tailoring per role
- ✅ GPT-5.2 cover letter generation
- ✅ Form automation (personal info, work history, education)
- ✅ Document upload (PDF resume + cover letter)
- ✅ CAPTCHA solving (2Captcha integration)
- ✅ Application submission and confirmation tracking

### ⚠️ Limitations

**Manual steps:**
- User must manually send networking outreach emails (no auto-send)
- User must manually update tracker with responses
- User must manually schedule interviews

**Not implemented:**
- Email sending via Gmail/Outlook API (security/privacy reasons)
- Automatic response parsing (requires email access)
- Calendar integration for interview scheduling
- Video interview question answering (requires human interaction)

---

## Control Commands

### Show Pipeline Status

```bash
python3 scripts/agent.py status --database applications.db
```

**Output:**
```text
📊 Job Search Pipeline Status

Phase 1: Company Discovery
  • Discovered: 50 companies
  • Researched: 20 companies
  • Targeted: 10 companies

Phase 3: Hiring Manager Discovery
  • Contacts found: 47
  • Emails verified: 43 deliverable
  • Social context: 10 enriched

Phase 5: Networking Outreach
  • Emails generated: 3
  • Sent: 3
  • Responses: 1 (33.3%)
  • Interviews: 0

Phase 7: ATS Applications
  • Job postings found: 12
  • Applications submitted: 12 (100%)
  • Confirmations received: 12
  • Responses: 0
  • Interviews: 0

Phase 6: Overall Tracking
  • Total touchpoints: 15 (3 outreach + 12 applications)
  • Pending follow-ups: 12
  • Active conversations: 1
  • Interview conversion: 0% (0/15)
```

### Update Tracker (After Sending Email)

```bash
python3 scripts/agent.py update-tracker \
  --id 42 \
  --status sent \
  --sent_date 2026-03-10 \
  --notes "Sent via Gmail, personalized with MLSys reference"
```

### Record Response

```bash
python3 scripts/agent.py record-response \
  --id 42 \
  --responded yes \
  --response_date 2026-03-12 \
  --next_step "Coffee chat scheduled for 3/18" \
  --status active
```

### Schedule Follow-Up

```bash
python3 scripts/agent.py schedule-followup \
  --id 42 \
  --followup_date 2026-03-17 \
  --template "polite_nudge" \
  --notes "Follow up if no response by end of week"
```

---

## Monitoring & Logs

### applications.db (SQLite)

**Schema:**
```sql
CREATE TABLE applications (
  id INTEGER PRIMARY KEY,
  company_name TEXT,
  company_domain TEXT,
  contact_name TEXT,
  contact_title TEXT,
  contact_email TEXT,
  contact_linkedin TEXT,
  outreach_email TEXT,
  sent_date DATE,
  response_date DATE,
  responded BOOLEAN,
  interview_scheduled BOOLEAN,
  interview_date DATE,
  status TEXT, -- pending, sent, responded, interview, offer, rejected
  followup_date DATE,
  notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### outreach.jsonl (JSONL Log)

One line per generated email:
```json
{"timestamp": "2026-03-10T14:00:00Z", "company": "Anthropic", "contact": "Sarah Chen", "subject": "Your MLSys talk + coffee at AI Safety Summit?", "body": "...", "personalization": {"hook": "MLSys talk", "value": "distributed training experience", "ask": "coffee at conference"}, "cost": 3.00}
```

### research.json (Company Research)

```json
{
  "companies": [
    {
      "name": "Anthropic",
      "domain": "anthropic.com",
      "culture": "Research-focused, safety-first",
      "tech_stack": ["Rust", "Python", "PyTorch"],
      "recent_news": "Launched Claude 4.6",
      "hiring_signals": ["3 job postings this week", "15% team growth"],
      "research_date": "2026-03-10",
      "cost": 0.22
    }
  ]
}
```

---

## Best Practices

### For Job Seekers

1. **Quality over quantity**: 3 perfect emails > 30 generic blasts
2. **Research deeply**: Read their blog, watch their talks, understand their tech
3. **Lead with value**: What can you contribute, not what you need
4. **Event-based networking**: In-person > cold email (40% vs 7% response rate)
5. **Follow up**: 70% of successful hires required 2+ touchpoints
6. **Track everything**: CRM discipline separates amateurs from pros

### For Developers (Claude)

1. **Validate email deliverability**: Always run AlphaGrowth verification before outreach
2. **Personalization is key**: Generic emails have <5% response rate
3. **Never spam**: 3-5 thoughtful emails > 50 mass blasts
4. **Cost transparency**: Show user exact costs before running each phase
5. **Privacy first**: Delete contact data after job search complete
6. **Follow ethical guidelines**: Respect platform ToS, no scraping private data

---

## Troubleshooting

### "No companies found"
- Loosen filters (employee range, funding stage)
- Try different industries or locations
- Check AlphaGrowth coverage for target market

### "No hiring managers found"
- Try broader title search ("Manager", "Director", "VP")
- Use Playwright as fallback to scrape LinkedIn company page
- Some companies don't list full org charts publicly

### "Low response rate (<10%)"
- Improve personalization (reference specific work, posts)
- Try event-based outreach instead of cold emails
- Check email deliverability (verify sender domain, SPF/DKIM)
- A/B test different subject lines

### "Email verification failed"
- AlphaGrowth may not have coverage for all domains
- Try alternate email formats (first.last@, flast@, etc.)
- Use Playwright to find email from LinkedIn profile

---

## Strategy: Taariq's Approach (From Blog Post)

### ❌ What Doesn't Work
- Submitting resume to ATS (Applicant Tracking System)
- Applying to 100+ jobs via job boards
- Waiting for recruiters to respond
- Generic LinkedIn InMails
- Mass cold emails

### ✅ What Works
1. **Identify decision-makers** (hiring managers, not HR)
2. **Network in person** (conferences, meetups, office events)
3. **Demonstrate value first** (reference their work, show expertise)
4. **Build relationships** (coffee chats, not transactional asks)
5. **Leverage warm intros** (mutual connections > cold outreach)

### Key Insight
> "The hiring manager already knows they need to hire. They're just waiting to meet someone great. Be that person they meet at the conference, not resume #247 in their inbox."

---

## AgentSkills.io Standard

This skill follows the [AgentSkills.io](https://agentskills.io) open standard for agent skills, ensuring compatibility across:
- Claude Code
- OpenAI Codex
- Google Gemini
- Any compatible LLM tool

Taariq Lewis, SerenAI, Paloma, and Volume at https://serendb.com
Email: hello@serendb.com
