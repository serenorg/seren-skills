---
name: skill-runtime
description: Shared runtime contract for executable seren skills that need stable config, env, state, and log paths outside the installed skill directory.
---

# Skill Runtime

Use this library skill when an executable Seren skill needs stable runtime file paths for `.env`, `config.json`, `state/`, or `logs/`.

## Runtime Contract

Installed skill directories are replaceable. User-managed runtime files live outside the install directory.

Shared runtime root:

- macOS/Linux: `$XDG_CONFIG_HOME/seren` with `~/.config/seren` fallback
- Windows: `%APPDATA%\seren`

Per-skill runtime directory:

- shared: `<runtime-root>/skills-data/<slug>/`
- project override: `<project>/.seren/skills-data/<slug>/`

Slug example:

- `coinbase/smart-dca-bot` -> `coinbase-smart-dca-bot`

Expected runtime files:

- `.env`
- `config.json`
- `state/`
- `logs/`

## Resolution Priority

For `.env` and `config.json`, resolve in this order:

1. explicit absolute path
2. explicit relative path with directory segments, resolved from the current working directory
3. project-level `.seren/skills-data/<slug>/` discovered by walking up from `cwd`
4. shared runtime root under XDG or `%APPDATA%`
5. legacy fallback in the installed skill directory, only if the file already exists there

If no file exists yet, return the preferred runtime location rather than the legacy install-directory path.

## Environment Override

Use `SEREN_SKILL_ENV_FILE` to override `.env` resolution when a skill needs a non-standard env file location.

## Legacy Fallback

Legacy skill-root files are still supported for backward compatibility, but they are deprecated.

When the resolver falls back to a skill-root file, it should emit a deprecation warning and point to the preferred runtime path under `skills-data/<slug>/`.

## Migration

Move legacy files from the skill install directory into the runtime directory:

- move `.env` to `<runtime-root>/skills-data/<slug>/.env`
- move `config.json` to `<runtime-root>/skills-data/<slug>/config.json`
- move `state/` to `<runtime-root>/skills-data/<slug>/state/`
- move `logs/` to `<runtime-root>/skills-data/<slug>/logs/`

Skill code should continue accepting explicit `--config` and `--env-file` style inputs when available, but defaults should resolve through this runtime contract.

## Integration Pattern

Consume this skill by generating a local `scripts/runtime_paths.py` into each executable skill.

Recommended entrypoint pattern:

1. import `activate_runtime` from the local `runtime_paths.py`
2. call `args.config = str(activate_runtime(args.config))` near process start
3. let existing relative `state/`, `logs/`, and `.env` usage resolve inside the runtime directory

This keeps executable skills self-contained at install time while preserving a single canonical source for the runtime contract.
