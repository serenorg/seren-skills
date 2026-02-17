# Seren Skills

Community-driven skills for [Seren Desktop](https://github.com/serenorg/seren-desktop). Skills teach AI agents how to use APIs, run autonomous workflows, and guide users through tasks.

## Structure

Skills are organized by org (or publisher), with each skill in a subdirectory:

```
seren-skills/
├── apollo/
│   └── api/                     # Apollo.io API integration
├── coinbase/
│   └── grid-trader/             # Automated grid trading bot
├── cryptobullseyezone/
│   └── tax/                     # 1099-DA to Form 8949 reconciliation guide
├── kraken/
│   └── grid-trader/             # Kraken grid trading bot
├── polymarket/
│   └── trader/                  # Polymarket prediction market bot
└── seren/
    ├── browser-automation/      # Playwright browser automation
    ├── getting-started/         # Getting started guide
    ├── job-seeker/              # Job search automation
    └── skill-creator/           # Skill creation guide
```

### Slugs

The slug is derived by joining the org and skill name with a hyphen:

```
coinbase/grid-trader     → coinbase-grid-trader
cryptobullseyezone/tax   → cryptobullseyezone-tax
polymarket/trader        → polymarket-trader
seren/getting-started    → seren-getting-started
seren/browser-automation → seren-browser-automation
```

Seren Desktop consumes skills by slug in a flat namespace.

## Adding a Skill

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

Quick version:

1. Create `<org>/<skill-name>/` at the repo root (use an existing org or create a new one)
2. Add a `SKILL.md` with required frontmatter
3. For agent skills, include runtime code and `requirements.txt` / `package.json`
4. Open a PR

## SKILL.md Frontmatter

Every skill needs a `SKILL.md` with YAML frontmatter:

```yaml
---
name: My Skill
description: "What the skill does and when to use it"
kind: agent              # agent | integration | guide
runtime: python          # python | node | bash | docs-only
author: Your Name
version: 1.0.0
tags: [relevant, tags]
publishers: [seren-publishers-used]    # optional
cost_estimate: "$X per operation"       # optional
---
```

The `kind` field describes the nature of the skill:

- **agent** — Autonomous bot that runs independently (has runtime code)
- **integration** — API documentation and usage patterns (SKILL.md only)
- **guide** — Tutorials and how-to content (SKILL.md only)
