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
- `scripts/runtime_paths.py` - generated helper for runtime config resolution when the skill needs stable runtime paths
- `requirements.txt` (python) or `package.json` (node) at skill root when needed
- `config.example.json` at skill root when needed
- `.env.example` at skill root when needed
- `.gitignore` for repo-local development files and secrets

```
coinbase/grid-trader/
├── SKILL.md               # Required - skill documentation
├── scripts/
│   ├── grid_trader.py     # Runtime code
│   └── runtime_paths.py   # Generated from seren/skill-runtime when needed
├── requirements.txt       # Python dependencies
├── package.json           # Node dependencies
├── config.example.json    # Configuration template
└── .env.example           # Environment template
```

Keep dependency/config templates (`requirements.txt`, `package.json`, `config.example.json`, `.env.example`) at the skill root, not inside `scripts/`.
Do not assume real `config.json` or `.env` files live in the installed skill directory. Seren refreshes installed skills by replacing the managed directory, so user-owned runtime files must live outside it:

- shared runtime root:
  - macOS/Linux: `$XDG_CONFIG_HOME/seren` with `~/.config/seren` fallback
  - Windows: `%APPDATA%\seren`
- global skill runtime files: `$SEREN_CONFIG_DIR/skills-data/<slug>/`
- project overrides: `<project>/.seren/skills-data/<slug>/`

Use `config.example.json` and `.env.example` as templates only. Skill runtimes should accept explicit `--config` and `--env-file` paths, or equivalent environment variables, so `seren` and `seren-desktop` can pass files from the runtime directory.

For Python skills, prefer generating a local `scripts/runtime_paths.py` from [`seren/skill-runtime`](./seren/skill-runtime/) and then using `activate_runtime()` near process start:

```python
from runtime_paths import activate_runtime

args.config = str(activate_runtime(args.config))
```

That pattern gives you:

- project override support via `<project>/.seren/skills-data/<slug>/`
- shared runtime config under XDG / `%APPDATA%`
- legacy skill-root fallback with a deprecation warning
- relative `state/` and `logs/` paths rooted in the runtime directory instead of the installed skill directory

If you are not using the shared helper yet, keep the resolution order simple. Resolve the env file in this order:

1. `--env-file` CLI argument
2. project override: `<project>/.seren/skills-data/<slug>/.env`
3. shared config root:
   - macOS/Linux: `$XDG_CONFIG_HOME/seren/skills-data/<slug>/.env`
   - Windows: `%APPDATA%\seren\skills-data\<slug>\.env`
4. fallback on macOS/Linux: `~/.config/seren/skills-data/<slug>/.env`

Minimal helper pattern:

```python
from pathlib import Path
import os


def default_skill_env_path(slug: str, project_root: str | None = None) -> Path:
    if project_root:
        project_env = Path(project_root) / ".seren" / "skills-data" / slug / ".env"
        if project_env.exists():
            return project_env

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "seren" / "skills-data" / slug / ".env"

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "seren" / "skills-data" / slug / ".env"

    return Path.home() / ".config" / "seren" / "skills-data" / slug / ".env"
```

Then pass the resolved path to `load_dotenv()` or your own parser if the file exists. If your skill uses the shared `activate_runtime()` pattern, relative `state/` and `logs/` paths will follow the runtime directory automatically.

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
