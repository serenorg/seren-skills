---
name: skill-creator
description: "Create or update skills that comply with the Agent Skills specification and Seren repo conventions."
---

# Skill Creator

Use this skill to create new skills or modernize existing ones in this repository.

## When To Use

Use this skill when the user asks to:

- create a new skill
- update skill frontmatter or layout
- make a skill compatible with Agent Skills-compliant tooling

## Canonical Standard

Treat the Agent Skills specification as the source of truth:

- https://agentskills.io/specification

Core spec rules:

- `SKILL.md` must include YAML frontmatter
- required top-level fields: `name`, `description`
- optional top-level fields: `license`, `compatibility`, `metadata`, `allowed-tools`
- `name` must:
  - be 1-64 chars
  - use lowercase letters, digits, and hyphens only
  - not start/end with a hyphen
  - not contain consecutive hyphens
  - exactly match the parent directory name
- if `metadata` is used, it must be a map of string keys to string values

## Seren Repo Conventions

On top of the spec, this repo uses the following conventions:

- frontmatter uses only `name` and `description` (everything else is derived)
- optional spec fields (`license`, `compatibility`, `allowed-tools`, `metadata`) are available when appropriate
- use the first `# H1` in the body as the display name
- keep runtime files in `scripts/`
- keep `requirements.txt` / `package.json`, `config.example.json`, and `.env.example` at the skill root
- keep local `config.json` untracked

## Workflow

### 1. Confirm Scope

Collect:

- org name (for example `seren`, `coinbase`)
- skill directory name in kebab-case (for example `grid-trader`)
- skill purpose and activation context
- whether the skill needs executable code (put in `scripts/`) or is documentation-only

### 2. Scaffold Directory

Create:

- `{org}/{skill-name}/SKILL.md`
- optional runtime files under `{org}/{skill-name}/scripts/`
- optional root files: `requirements.txt`, `package.json`, `config.example.json`, `.env.example`

### 3. Write Frontmatter Correctly

Use this baseline template:

```yaml
---
name: skill-name
description: Clear statement of what the skill does and when to use it
# license: Apache-2.0
# compatibility: "Requires git and jq"
# allowed-tools: Read Bash(git:*)
---
```

Validation requirements:

- `name` equals directory name exactly
- `description` is concrete and trigger-oriented
- optional fields (if present) follow spec constraints
- keep frontmatter fields minimal and relevant

### 4. Write A Focused SKILL.md Body

Include:

- when to use the skill
- step-by-step workflow
- minimal command examples
- constraints and failure handling

Avoid:

- long generic prose
- duplicate documentation across many files
- references to paths that do not exist

### 5. Validate Before PR

Checklist:

- frontmatter present and valid
- `name` matches directory
- commands in docs point to real files
- runtime files (if any) live under `scripts/`
- no secrets committed

## Example Skeleton

```markdown
---
name: api-tester
description: Test REST endpoints and validate response contracts.
---

# API Tester

## When to Use

Use when a user asks to validate API behavior or contracts.

## Workflow

1. Collect endpoint and auth details.
2. Run a defined test matrix (happy path + failures).
3. Report findings with reproducible commands.
```
