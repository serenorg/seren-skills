# Seren Skills

Community-driven skills for [Seren Desktop](https://github.com/serenorg/seren-desktop). Skills teach AI agents how to use APIs, run autonomous workflows, and guide users through tasks.

## Standard

This repository follows the [Agent Skills specification](https://agentskills.io/specification).

Spec rules we enforce:

- Required top-level fields: `name`, `description`
- Optional top-level fields: `license`, `compatibility`, `metadata`, `allowed-tools`
- `name` must:
  - be 1-64 chars
  - use lowercase letters, digits, and hyphens only
  - not start/end with a hyphen
  - not contain consecutive hyphens
  - exactly match the parent directory name
- `description` must be non-empty and <= 1024 chars
- `metadata` must be a map of string keys to string values

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
coinbase/grid-trader     -> coinbase-grid-trader
cryptobullseyezone/tax   -> cryptobullseyezone-tax
polymarket/trader        -> polymarket-trader
seren/getting-started    -> seren-getting-started
seren/browser-automation -> seren-browser-automation
```

Seren Desktop consumes skills by slug in a flat namespace.

## Skill Directory Layout

```
org/skill-name/
├── SKILL.md               # Required - docs and frontmatter
├── scripts/               # Executable code (agent skills only)
│   └── agent.py
├── requirements.txt       # Python dependencies
├── package.json           # Node dependencies
├── config.example.json    # Config template (optional)
└── .env.example           # Environment template (optional)
```

## Adding a Skill

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

Quick version:

1. Create `<org>/<skill-name>/` at the repo root
2. Add a `SKILL.md` with valid frontmatter where `name` equals `<skill-name>`
3. For agent skills, put runtime code in `scripts/` and keep dependency/config templates at the skill root
4. Open a PR

## SKILL.md Frontmatter

```yaml
---
name: skill-name
description: What the skill does and when to use it
license: Apache-2.0 # optional
compatibility: "Requires git and jq" # optional
allowed-tools: Bash(git:*) Read # optional, experimental
---
```

Conventions:

- Use the first `# H1` in the document body as the display name
- Keep runtime code in `scripts/`
- `metadata` is available per spec but not used by Seren skills today
