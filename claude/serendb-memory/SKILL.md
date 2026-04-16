---
name: serendb-memory
display-name: "Claude SerenDB Memory"
description: "Sync Claude Code auto-memory files into SerenDB for CLI-only users without Seren Desktop."
compatibility: "Claude Code CLI only. Do not invoke from Seren Desktop."
---

# Serendb Memory

## Overview

This skill moves Claude Code auto-memory out of plaintext markdown files under
`~/.claude/projects/*/memory/` and into SerenDB-backed cloud memory.

## When to Use

- sync claude code memory to serendb
- install claude memory watcher
- migrate claude memory files

## Important

- This skill is for non-SerenDesktop users.
- If Desktop runtime markers are detected, the runtime exits immediately.
- The watcher deletes plaintext memory files after cloud persistence or encrypted queueing.

## Commands

```bash
python scripts/agent.py install --config config.json
python scripts/agent.py start --foreground --config config.json
python scripts/agent.py status --config config.json
python scripts/agent.py doctor --config config.json
python scripts/agent.py migrate --config config.json
python scripts/agent.py flush --config config.json
python scripts/agent.py export --config config.json --output-dir ./exports
python scripts/agent.py stop --config config.json
python scripts/agent.py uninstall --config config.json
```

## What `install` does

1. Bootstraps or reuses `SEREN_API_KEY`
2. Creates the local encrypted queue/state DB in `~/.seren/claude-serendb-memory`
3. Migrates existing `~/.claude/projects/*/memory/*.md` files
4. Writes `MEMORY.md` back from cloud state
5. Installs a LaunchAgent on macOS or a user-level systemd service on Linux

## Runtime guarantees

- New memory files are parsed and pushed to SerenDB
- If the network is down, the file payload is queued in encrypted local storage
- After queueing or persistence, the plaintext file is removed
- `MEMORY.md` is atomically rewritten from cloud state
- Re-seeing the same file content is idempotent and does not create duplicate writes

## Workflow Summary

1. `install` bootstraps auth and installs the background watcher
2. `start --foreground` runs the watcher loop in the current process
3. `migrate` or `flush` performs one immediate sync pass
4. `status`, `doctor`, `stop`, `uninstall`, and `export` manage the local service
