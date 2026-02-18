# Job Seeker & Apply

AI-powered job search agent that automates Taariq Lewis's proven networking strategy: find target companies, research them, discover hiring managers via Apollo.io, generate personalized outreach, track applications, and auto-apply via ATS platforms.

## Features

- **Phase 0**: Extract user profile from resume + LinkedIn export
- **Phase 1**: Discover companies (AlphaGrowth - 50 companies)
- **Phase 2**: Research companies (Perplexity + Exa - top 20)
- **Phase 3**: Find hiring managers (Apollo.io - 100 contacts)
- **Phase 4**: Discover networking events (Exa)
- **Phase 5a**: Verify emails (AlphaGrowth)
- **Phase 5b**: Generate personalized outreach (GPT-5.2)
- **Phase 6**: Track applications (SQLite CRM)
- **Phase 7**: Auto-apply to jobs (Playwright + 2Captcha) *(coming soon)*

## The Double-Tap Strategy

1. Apply via ATS → Get confirmation ID
2. Email hiring manager → Reference application ID
3. Result: Maximum visibility (HR sees application + hiring manager knows to look for it)

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up Environment

```bash
# Copy example env file
cp .env.example .env

# Add your Seren API key
echo "SEREN_API_KEY=sb_your_key_here" > .env
```

Get your Seren API key at: https://serendb.com

### 3. Download LinkedIn Data Export

**REQUIRED**: You must download your LinkedIn data export before running Phase 0.

1. Go to [linkedin.com/mypreferences/d/download-my-data](https://linkedin.com/mypreferences/d/download-my-data)
2. Select "Download larger data archive" → Check all data types
3. Click "Request archive" (takes 10-15 minutes)
4. Download ZIP file when ready

### 4. Run the Job Search

```bash
# Phase 0: Extract your profile
python scripts/agent.py extract-profile \
  --resume resume.pdf \
  --linkedin-export linkedin_export.zip \
  --output profile.json

# Phase 1: Discover companies
python scripts/agent.py discover \
  --profile profile.json \
  --role "Senior ML Engineer" \
  --industry "Artificial Intelligence" \
  --location "San Francisco" \
  --limit 50 \
  --output companies.json

# Phase 2: Research top companies
python scripts/agent.py research \
  --companies companies.json \
  --limit 20 \
  --output research.json

# Phase 3: Find hiring managers
python scripts/agent.py find-contacts \
  --companies research.json \
  --titles "Engineering Manager,VP Engineering,Director of Engineering" \
  --limit 10 \
  --output contacts.json

# Phase 4: Discover networking events
python scripts/agent.py discover-events \
  --location "San Francisco" \
  --industry "AI,Machine Learning" \
  --date-range "2026-03-01,2026-04-30" \
  --output events.json

# Phase 5a: Verify emails
python scripts/agent.py verify-emails \
  --contacts contacts.json \
  --output contacts_verified.json

# Phase 5b: Generate outreach emails
python scripts/agent.py generate-outreach \
  --contacts contacts_verified.json \
  --profile profile.json \
  --events events.json \
  --limit 3 \
  --output outreach.json

# Phase 6: Initialize tracker
python scripts/agent.py init-tracker --database job_seeker.db

# Phase 6: Check status
python scripts/agent.py status --database job_seeker.db --campaign "ML_2026"
```

## Dry-Run Mode

Test the workflow without making API calls (zero cost):

```bash
python scripts/agent.py discover --profile profile.json --role "Engineer" --industry "Tech" --location "SF" --dry-run
```

## Cost Estimates

- **Phase 0**: $0.50 (GPT-5.2 parsing)
- **Phase 1**: $1.50 (50 companies @ $0.03 each)
- **Phase 2**: $4.40 (20 companies @ $0.22 each)
- **Phase 3**: $4.00 (100 contacts @ $0.04 each)
- **Phase 4**: $0.40 (10 events)
- **Phase 5a**: $0.50 (50 emails @ $0.01 each)
- **Phase 5b**: $9.00 (3 emails @ $3.00 each)
- **Phase 6**: $0.00 (local SQLite)
- **Phase 7**: $36.00 (12 applications @ $3.00 each) *(coming soon)*

**Total**: $20.30 for networking strategy, $56.30 with auto-apply

## Database Schema

Phase 6 creates a SQLite database with the following tables:

- `campaigns`: Job search campaigns
- `companies`: Target companies
- `contacts`: Hiring managers and contacts
- `outreach`: Outreach emails sent
- `applications`: Job applications submitted
- `events`: Networking events

## Logs

All operations are logged to `logs/` directory as JSONL files:

- `profile_extraction.jsonl`
- `company_discovery.jsonl`
- `company_research.jsonl`
- `contact_discovery.jsonl`
- `email_verification.jsonl`
- `outreach_generation.jsonl`
- `applications.jsonl`
- `events.jsonl`

## Troubleshooting

### "SEREN_API_KEY is required"

Make sure you've created a `.env` file with your API key:

```bash
echo "SEREN_API_KEY=sb_your_key_here" > .env
```

### "Failed to parse PDF"

Install PyPDF2:

```bash
pip install PyPDF2
```

### "LinkedIn export not found"

Download your LinkedIn data export first (see Quick Start step 3).

## Support

- Seren Docs: https://docs.serendb.com
- Issues: https://github.com/serenorg/seren-desktop-issues

## License

MIT License - See main repository for details.

---

**Taariq Lewis, SerenAI, Paloma, and Volume at https://serendb.com**
**Email: hello@serendb.com**
