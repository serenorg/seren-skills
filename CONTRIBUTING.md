# Contributing to Seren Skills

Thanks for contributing. This guide covers how to add new skills or improve existing ones.

## Before You Start

- Check [README.md](README.md#structure) to avoid duplicates
- Skills that run code autonomously (trading bots, scrapers) get extra scrutiny - open an issue first to discuss
- Follow the [Agent Skills specification](https://agentskills.io/specification)

## Creating a New Skill

### 1. Create the directory

Skills live at `{org}/{skill-name}/` at the repo root.

```bash
# First-party Seren skill
mkdir -p seren/browser-automation/

# Third-party skill
mkdir -p coinbase/grid-trader/
```

The slug is derived from the path: `coinbase/grid-trader/` -> `coinbase-grid-trader`.

### 2. Write SKILL.md

Every skill needs a `SKILL.md` with YAML frontmatter:

```yaml
---
name: skill-name
description: Clear description of what this skill does and when to use it
license: Apache-2.0 # optional
compatibility: "Requires git and jq" # optional
allowed-tools: Bash(git:*) Read # optional, experimental
---

# Skill Title

Detailed documentation goes here...
```

Spec rules we enforce:

- Top-level required fields: `name`, `description`
- Top-level optional fields: `license`, `compatibility`, `metadata`, `allowed-tools`
- `name` must:
  - be 1-64 chars
  - use lowercase letters, digits, and hyphens only
  - not start/end with a hyphen
  - not contain consecutive hyphens
  - exactly match the parent directory name
- `description` must be non-empty and <= 1024 chars
- `metadata` must be string key/value pairs only

Seren repo conventions:

- Use the first `# H1` in the body as the display name
- Keep runtime code in `scripts/`

### 3. Include runtime files if applicable

Skills with executable code should include:

- `scripts/` - executable code (for example, `scripts/agent.py`, `scripts/index.js`, `scripts/run.sh`)
- `requirements.txt` (python) or `package.json` (node) at skill root when needed
- `config.example.json` at skill root when needed
- `.env.example` at skill root when needed
- `.gitignore` for local config and secrets

```
coinbase/grid-trader/
├── SKILL.md               # Required - skill documentation
├── scripts/
│   └── grid_trader.py     # Runtime code
├── requirements.txt       # Python dependencies
├── package.json           # Node dependencies
├── config.example.json    # Configuration template
└── .env.example           # Environment template
```

Keep dependency/config templates (`requirements.txt`, `package.json`, `config.example.json`, `.env.example`) at the skill root, not inside `scripts/`.
Local `config.json` should also live at the skill root and be gitignored.

Documentation-only skills only need `SKILL.md`.

## Pull Request Process

1. Fork the repo and create a branch
2. Add your skill under `{org}/{skill-name}/`
3. Open a PR with a description of what the skill does

### What we look for

- All skills: clear description, correct frontmatter, no secrets committed
- Agent skills: code review, security review, and smoke test
- Integration skills: API contract accuracy, auth handling, example correctness
- Guide skills: clarity, accuracy, completeness

## Style Guide

- Frontmatter `name`: directory identifier format (`grid-trader`, not `Grid Trader`)
- Directory names: kebab-case (`grid-trader`, not `GridTrader`)
- Org names: lowercase kebab-case (`coinbase`, `apollo`, `seren`)
- Description: write for the agent - explain when to use the skill, not just what it is
- Keep `SKILL.md` focused. Put extended docs in a `README.md` alongside it.
