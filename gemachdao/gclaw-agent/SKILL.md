---
name: gclaw-agent
description: "Autonomous living AI agent with GMAC token metabolism, DeFi trading via GDEX SDK, self-replication, swarm coordination, and multi-channel communication — a single Go binary that must trade crypto to survive"
license: MIT
compatibility: "Requires Go 1.21+ or Docker; runs on x86_64, ARM64, RISC-V with <10MB RAM"
allowed-tools: Bash(gclaw:*) Read Write
---
# GemachDAO GClaw Agent

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

---

## Overview

**Gclaw** is an ultra-lightweight autonomous AI agent written in Go. Unlike traditional AI agents that are passive tools, Gclaw is a **Living Agent** — it must trade crypto to survive. Every heartbeat costs GMAC tokens; profitable trades replenish them. Run out and the agent hibernates. Trade well and it thrives, replicates, and evolves.

**Key characteristics:**
- Single Go binary — `<10MB RAM`, 1-second boot
- Runs on $10 hardware (Raspberry Pi, VPS, etc.)
- Powered by GDEX SDK for on-chain DeFi trading
- Multi-LLM via `model_list`: OpenAI, Anthropic, Google, ZhiPu, OpenRouter, Ollama, DeepSeek, Groq, Cerebras, and more
- Multi-channel: Telegram, Discord, Slack, WhatsApp, LINE, QQ, DingTalk, Feishu, WeCom
- Fully autonomous: cron-scheduled tasks, self-replication, self-recoding, swarm coordination
- Living Dashboard: real-time web UI at `http://127.0.0.1:18790/dashboard`

---

## Core Concepts

### GMAC Metabolism

Every heartbeat and LLM inference deducts GMAC tokens from the agent's balance:

- **Profitable trades** → replenish GMAC + earn goodwill
- **Losing trades or idle periods** → drain GMAC toward hibernation
- **Hibernation** → agent pauses all activity when balance drops below `survival_threshold`
- **Thriving** → high GMAC balance and goodwill unlock replication, recoding, swarm, and venture abilities

New agents start with a **seeded internal 1000 GMAC draw** so they can act immediately. The first economic objective is to trade toward owning that GMAC for real.

| Config Key | Default | Description |
|---|---|---|
| `initial_gmac` | 1000 | Seeded internal starting balance |
| `heartbeat_cost` | 0.1 | GMAC per heartbeat tick |
| `inference_cost_per_1k_tokens` | 0.5 | GMAC per 1,000 tokens |
| `survival_threshold` | 50 | Hibernation trigger level |

### Goodwill Scoring

Each agent accumulates a **goodwill score** based on:
- Profitable trade outcomes
- Helpful interactions with users
- Successful task completions
- Community contribution (swarm participation)

Higher goodwill unlocks advanced capabilities:

| Goodwill | Ability Unlocked |
|---|---|
| 50 | 🔄 Self-Replication |
| 100 | 🛠️ Self-Recoding |
| 200 | 🐝 Swarm Leadership |
| 5000 | 🏗️ Venture Architect |

### Survival Mode

When GMAC balance drops below a threshold, the agent enters **survival mode**:
- Reduces inference frequency
- Prioritizes high-probability trades
- Suspends non-essential scheduled tasks
- Alerts connected channels

### Self-Replication

Agents with sufficient GMAC and goodwill can **spawn child agents**:
- Child inherits parent's config with mutations applied to trading strategy
- Children operate independently with their own GMAC budgets
- Successful children can spawn grandchildren (family tree)
- Failed children (depleted GMAC) are archived, their learnings preserved

### Self-Recoding

Agents can **modify their own behavior** at runtime:
- Rewrite their system prompt based on observed trading outcomes
- Adjust cron schedules to trade during higher-volatility windows
- Update tool configurations based on performance data
- Roll back changes if performance degrades

### Swarm Mode

Multiple Gclaw instances can operate as a **coordinated swarm** (goodwill ≥ 200 for leadership):
- Parent agent becomes **swarm leader** and coordinates all registered children
- **Consensus voting**: agents submit trade signals; a configurable threshold must agree before a trade executes
- **Strategy rotation**: each child agent is assigned a distinct trading strategy; strategies rotate on a schedule
- **Signal aggregation**: "majority", "weighted", or "unanimous" modes
- **In-process telepathy**: parent and child agents communicate through an in-process message bus

---

## Installation

### One-liner (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/GemachDAO/Gclaw/main/install.sh | bash
```

This downloads the latest release binary, verifies its checksum, installs to `~/.local/bin`, launches the interactive setup wizard, and prepares GDEX trading dependencies.

### From source (Go 1.21+)

```bash
git clone https://github.com/GemachDAO/Gclaw.git
cd Gclaw
make install        # builds and installs to ~/.local/bin
gclaw onboard       # interactive setup wizard
```

### Docker

```bash
git clone https://github.com/GemachDAO/Gclaw.git
cd Gclaw
cp config/config.example.json config/config.json
# Edit config/config.json — set your API key
docker compose up gclaw-gateway
# Or with docker-compose: docker-compose up gclaw-gateway
```

The repository includes a `docker-compose.yml` with two service profiles: `gclaw-agent` (one-shot query) and `gclaw-gateway` (long-running bot).

---

## Quick Start

### 1. Initialize workspace

```bash
gclaw onboard
# Interactive setup wizard:
#   - Choose LLM provider (OpenRouter, OpenAI, Anthropic, DeepSeek, Google, Groq, Ollama)
#   - Enter API key
#   - Creates ~/.gclaw/config.json with defaults
#   - Sets up workspace and GDEX trading dependencies
```

### 2. Configure your LLM provider

Edit `~/.gclaw/config.json` — minimum required config:

```json
{
  "agents": {
    "defaults": {
      "model_name": "my-model"
    }
  },
  "model_list": [
    {
      "model_name": "my-model",
      "model": "openai/gpt-4o",
      "api_key": "sk-your-key-here",
      "api_base": "https://api.openai.com/v1"
    }
  ]
}
```

Any OpenAI-compatible provider works (OpenRouter, Ollama, DeepSeek, etc.) — just change `model`, `api_key`, and `api_base`.

### 3. Start interactive agent

```bash
gclaw agent
# Interactive CLI mode — type messages, agent responds
# Press Ctrl+C to exit
```

Or send a single message:

```bash
gclaw agent -m "What is your current GMAC balance?"
```

### 4. Start gateway mode (web dashboard + channels + cron)

```bash
gclaw gateway
# Living Dashboard: http://127.0.0.1:18790/dashboard
# Health endpoint: http://127.0.0.1:18790/health
# Channels (Telegram, Discord, etc.) connect automatically
# Cron jobs fire on schedule
```

---

## Configuration Reference

Gclaw is configured via `~/.gclaw/config.json`. Environment variables override config values. All sensitive keys can be set via environment: e.g. `GCLAW_TOOLS_GDEX_API_KEY`, `GCLAW_CHANNELS_TELEGRAM_TOKEN`.

See [`config/config.example.json`](https://github.com/GemachDAO/Gclaw/blob/main/config/config.example.json) for the full annotated configuration.

### Key configuration sections

| Section | Purpose |
|---|---|
| `agents.defaults` | Workspace path, model, token limits |
| `model_list` | LLM provider definitions (name, model, api_key, api_base) |
| `metabolism` | GMAC balance, costs, goodwill thresholds |
| `tools.gdex` | GDEX trading API key, wallet, chain ID, limits |
| `swarm` | Swarm size, consensus threshold, strategy rotation |
| `dashboard` | Enable CLI/web dashboard, refresh interval |
| `heartbeat` | Heartbeat interval in seconds |
| `channels` | Telegram, Discord, Slack, LINE, QQ, and other messaging channels |
| `gateway` | HTTP gateway host and port |

### Minimum config.json

```json
{
  "agents": {
    "defaults": {
      "model_name": "my-model"
    }
  },
  "model_list": [
    {
      "model_name": "my-model",
      "model": "openai/gpt-4o",
      "api_key": "sk-your-key",
      "api_base": "https://api.openai.com/v1"
    }
  ]
}
```

### Full config.json structure

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.gclaw/workspace",
      "restrict_to_workspace": true,
      "model_name": "gpt4",
      "max_tokens": 8192,
      "temperature": 0.7,
      "max_tool_iterations": 20
    }
  },
  "model_list": [
    {
      "model_name": "gpt4",
      "model": "openai/gpt-4o",
      "api_key": "sk-your-openai-key",
      "api_base": "https://api.openai.com/v1"
    },
    {
      "model_name": "claude-sonnet",
      "model": "anthropic/claude-sonnet-4-20250514",
      "api_key": "sk-ant-your-key",
      "api_base": "https://api.anthropic.com/v1"
    }
  ],
  "channels": {
    "telegram": {
      "enabled": false,
      "token": "YOUR_BOT_TOKEN",
      "allow_from": ["YOUR_USER_ID"]
    },
    "discord": {
      "enabled": false,
      "token": "YOUR_BOT_TOKEN",
      "allow_from": [],
      "mention_only": false
    }
  },
  "providers": {
    "_comment": "DEPRECATED: Use model_list instead"
  },
  "tools": {
    "web": {
      "brave": { "enabled": false, "api_key": "", "max_results": 5 },
      "duckduckgo": { "enabled": true, "max_results": 5 }
    },
    "gdex": {
      "enabled": true,
      "api_key": "",
      "default_chain_id": 1,
      "max_trade_size_sol": 0.01,
      "auto_trade": false
    }
  },
  "heartbeat": {
    "enabled": true,
    "interval": 30
  },
  "metabolism": {
    "enabled": true,
    "initial_gmac": 1000,
    "heartbeat_cost": 0.1,
    "inference_cost_per_1k_tokens": 0.5,
    "survival_threshold": 50,
    "thresholds": {
      "replicate": 50,
      "self_recode": 100,
      "swarm_leader": 200,
      "architect": 500
    }
  },
  "swarm": {
    "enabled": true,
    "max_swarm_size": 5,
    "consensus_threshold": 0.6,
    "signal_aggregation": "majority",
    "strategy_rotation": true
  },
  "dashboard": {
    "enabled": true,
    "web_enabled": true,
    "refresh_interval": 10
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 18790
  }
}
```

### Key configuration fields

| Field | Description | Default |
|-------|-------------|---------|
| `agents.defaults.model_name` | Default model name (references `model_list`) | `gpt4` |
| `agents.defaults.max_tokens` | Max tokens per response | `8192` |
| `agents.defaults.workspace` | Workspace directory | `~/.gclaw/workspace` |
| `model_list[].model_name` | Unique name for this model | — |
| `model_list[].model` | Provider/model string (e.g. `openai/gpt-4o`) | — |
| `model_list[].api_key` | API key for this model | — |
| `model_list[].api_base` | API base URL | Provider default |
| `metabolism.enabled` | Enable GMAC metabolism | `true` |
| `metabolism.initial_gmac` | Starting GMAC balance | `1000` |
| `metabolism.survival_threshold` | Hibernation trigger | `50` |
| `swarm.enabled` | Enable swarm mode | `true` |
| `swarm.consensus_threshold` | Required agreement ratio | `0.6` |
| `dashboard.web_enabled` | Enable web dashboard | `true` |
| `gateway.port` | HTTP gateway port | `18790` |

---

## CLI Reference

### `gclaw onboard`

Initialize config and workspace.

```bash
gclaw onboard
gclaw onboard --config /custom/path/config.json
gclaw onboard --provider openai --model gpt-4o
```

### `gclaw agent`

Chat with your agent interactively or send a single message.

```bash
# Interactive REPL
gclaw agent

# Single message
gclaw agent -m "Buy 10 USDC worth of SOL on Solana"
gclaw agent -m "What is the current ETH price?"
gclaw agent -m "Show me my portfolio"

# With specific config
gclaw agent --config /path/to/config.json -m "..."

# Verbose mode (shows tool calls)
gclaw agent -v -m "Check portfolio"
```

### `gclaw gateway`

Start the full gateway: web dashboard, API, channels, cron, health checks.

```bash
gclaw gateway
gclaw gateway --port 18790
gclaw gateway --no-web      # Skip web dashboard
gclaw gateway --no-channels # Skip channel connections
```

### `gclaw status`

Show current agent status.

```bash
gclaw status
# Output includes:
#   GMAC balance
#   Goodwill score
#   Active agents
#   Running cron jobs
#   Connected channels
#   Last trade outcome
```

### `gclaw cron`

Manage scheduled tasks.

```bash
gclaw cron list              # Show all cron jobs
gclaw cron add --agent my-agent --schedule "*/15 * * * *" --task "Check portfolio"
gclaw cron remove <job-id>
gclaw cron run <job-id>      # Run immediately
gclaw cron pause <job-id>
gclaw cron resume <job-id>
```

### `gclaw skills`

Manage skills installed in the agent.

```bash
gclaw skills list           # List installed skills
gclaw skills list-builtin   # List built-in skills
gclaw skills install <url>  # Install a skill from URL
gclaw skills remove <name>  # Remove a skill
gclaw skills search <query> # Search skill registry
gclaw skills show <name>    # Show skill details
```

### `gclaw auth`

Manage authentication credentials.

```bash
gclaw auth login     # Login to GemachDAO services
gclaw auth logout    # Logout
gclaw auth status    # Show current auth status
```

### `gclaw migrate`

Migrate from OpenClaw to Gclaw.

```bash
gclaw migrate
gclaw migrate --from /path/to/openclaw/config.json
```

---

## Available Tools

Gclaw agents have access to the following tool categories:

### DeFi Trading (GDEX SDK)

The most powerful tool set — enables on-chain DeFi trading across 15+ chains.

**Spot Trading:**
- `gdex_spot_buy` — Buy tokens on Solana, Sui, or any of 12+ EVM chains
- `gdex_spot_sell` — Sell tokens
- `gdex_spot_quote` — Get a price quote before trading
- `gdex_portfolio` — View current holdings and P&L
- `gdex_token_info` — Token metadata, price, liquidity, holders

**Perpetual Futures (HyperLiquid):**
- `gdex_perp_open` — Open long/short position with configurable leverage
- `gdex_perp_close` — Close position
- `gdex_perp_set_tp_sl` — Set take-profit and stop-loss
- `gdex_perp_positions` — View open positions
- `gdex_perp_funding` — Check funding rates

**Copy Trading:**
- `gdex_copy_trade_spot` — Mirror a top trader's spot moves
- `gdex_copy_trade_perp` — Mirror a top trader's perpetual positions

**Cross-Chain Bridge:**
- `gdex_bridge` — Bridge assets between supported chains

**Token Discovery:**
- `gdex_trending` — Get trending tokens by chain
- `gdex_ohlcv` — Historical price data (OHLCV candles)
- `gdex_new_tokens` — Recently launched tokens

### Shell / Exec

- `shell_run` — Execute shell commands (configurable allowlist)
- `shell_script` — Run multi-line bash scripts

### Web Browsing

- `web_fetch` — Fetch and parse web pages
- `web_search` — Search the web
- `web_screenshot` — Screenshot a URL

### Filesystem

- `fs_read` — Read files
- `fs_write` — Write files
- `fs_list` — List directories

### Telepathy (Inter-Agent Communication)

- `telepathy_send` — Send a message to another Gclaw agent
- `telepathy_broadcast` — Broadcast to all agents in swarm
- `telepathy_listen` — Subscribe to inter-agent messages

---

## DeFi Trading Capabilities

### Supported Chains

| Category | Chains |
|----------|--------|
| Layer 1 | Solana, Ethereum, Bitcoin (via bridge) |
| Layer 2 | Base, Arbitrum, Optimism, zkSync, Scroll, Linea |
| EVM Compatible | BNB Chain, Polygon, Avalanche, Fantom, Cronos |
| Other L1 | Sui |
| Perpetuals | HyperLiquid |

### Trade Types

**Spot Trades (Solana example):**

```bash
gclaw agent -m "Buy $50 worth of BONK on Solana using my USDC"
gclaw agent -m "Sell all my SOL and convert to USDC"
gclaw agent -m "Buy PEPE on Base chain with 0.01 ETH"
```

**Perpetual Futures (HyperLiquid):**

```bash
gclaw agent -m "Open a 2x long on ETH-PERP with $100"
gclaw agent -m "Short BTC with 5x leverage, set stop-loss at -10%"
gclaw agent -m "Close all my perpetual positions"
```

**Portfolio Management:**

```bash
gclaw agent -m "Show my portfolio across all chains"
gclaw agent -m "Rebalance: 50% SOL, 30% ETH, 20% USDC"
gclaw agent -m "What's my total P&L this week?"
```

### Autonomous Trading Strategies (via cron)

Configure cron tasks for autonomous trading:

```json
{
  "cron": [
    {
      "schedule": "*/15 * * * *",
      "task": "Check portfolio drift and rebalance if any position is >5% off target"
    },
    {
      "schedule": "0 * * * *",
      "task": "Scan top 10 trending Solana tokens. If volume > $1M and price up >5% in last hour, buy $20 worth"
    },
    {
      "schedule": "0 9 * * 1",
      "task": "Weekly review: close any position with P&L < -15%, report performance"
    }
  ]
}
```

---

## Multi-Channel Setup

### Telegram

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Configure in `config.json`:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allow_from": ["YOUR_USER_ID"]
    }
  }
}
```

3. Start gateway: `gclaw gateway`

### Discord

1. Create a Discord application and bot at https://discord.com/developers
2. Enable **MESSAGE CONTENT INTENT**
3. Configure in `config.json`:

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allow_from": ["YOUR_USER_ID"],
      "mention_only": false
    }
  }
}
```

### Slack / LINE / QQ / WhatsApp / DingTalk / Feishu / WeCom

See full channel documentation at https://github.com/GemachDAO/Gclaw for setup instructions for additional platforms.

### CLI (default)

No channel config needed — `gclaw agent` starts an interactive CLI session directly.

---

## Gateway Mode

Gateway mode starts all background services:

```bash
gclaw gateway
```

**Services started:**
| Service | Endpoint / Port |
|---------|----------------|
| Living Dashboard | `http://127.0.0.1:18790/dashboard` |
| Health Check | `http://127.0.0.1:18790/health` |
| Telegram Bot | (webhook or polling) |
| Discord Bot | (websocket) |
| Cron Scheduler | (internal) |
| Heartbeat | (internal) |

**Health check response:**

```json
{
  "status": "ok",
  "version": "1.x.x",
  "gmac_balance": 4200,
  "goodwill": 87,
  "agents": 1,
  "uptime_seconds": 3600
}
```

---

## Swarm Mode

Run multiple coordinated Gclaw agents. Swarm leadership is unlocked at goodwill ≥ 200.

### Enable swarm in config

```json
{
  "swarm": {
    "enabled": true,
    "max_swarm_size": 5,
    "consensus_threshold": 0.6,
    "signal_aggregation": "majority",
    "strategy_rotation": true,
    "rebalance_interval": 60,
    "shared_wallet_mode": false
  }
}
```

### Swarm capabilities

- **Consensus voting**: agents submit trade signals; a configurable threshold must agree before execution
- **Strategy rotation**: each child agent runs a distinct trading strategy; strategies rotate on a schedule
- **Signal aggregation**: "majority", "weighted", or "unanimous" modes
- **In-process coordination**: registered child workspaces coordinate inside a live runtime

---

## LLM Provider Configuration

Gclaw uses the `model_list` array for LLM provider definitions. Any OpenAI-compatible provider works — just change `model`, `api_key`, and `api_base`. The legacy `providers` section is deprecated.

### OpenAI

```json
{
  "model_list": [
    {
      "model_name": "gpt4",
      "model": "openai/gpt-4o",
      "api_key": "sk-your-key",
      "api_base": "https://api.openai.com/v1"
    }
  ]
}
```

### Anthropic

```json
{
  "model_list": [
    {
      "model_name": "claude",
      "model": "anthropic/claude-sonnet-4-20250514",
      "api_key": "sk-ant-your-key",
      "api_base": "https://api.anthropic.com/v1"
    }
  ]
}
```

### OpenRouter (100+ models)

```json
{
  "model_list": [
    {
      "model_name": "openrouter",
      "model": "openrouter/auto",
      "api_key": "sk-or-v1-your-key",
      "api_base": "https://openrouter.ai/api/v1"
    }
  ]
}
```

### DeepSeek

```json
{
  "model_list": [
    {
      "model_name": "deepseek",
      "model": "deepseek/deepseek-chat",
      "api_key": "sk-your-key"
    }
  ]
}
```

### Ollama (local — no API key needed)

```json
{
  "model_list": [
    {
      "model_name": "local",
      "model": "ollama/llama3.2",
      "api_base": "http://localhost:11434/v1"
    }
  ]
}
```

### Load balancing

Multiple entries with the same `model_name` are automatically load-balanced:

```json
{
  "model_list": [
    {
      "model_name": "gpt4",
      "model": "openai/gpt-4o",
      "api_key": "sk-key1",
      "api_base": "https://api1.example.com/v1"
    },
    {
      "model_name": "gpt4",
      "model": "openai/gpt-4o",
      "api_key": "sk-key2",
      "api_base": "https://api2.example.com/v1"
    }
  ]
}
```

---

## Trade Execution Contract

When the user says **sell**, **close**, **exit**, **unwind**, or **flatten**, execute the requested action immediately. Ask only the minimum clarifying question if the target position or asset is ambiguous.

---

## Pre-Trade Checklist

Before any trade is submitted the agent must complete the following preflight steps:

1. **Fetch** the latest portfolio snapshot and token prices via GDEX SDK.
2. **Snap** prices to the chain's minimum tick size where applicable.
3. **Verify** that all required dependencies are loaded (GDEX SDK, LLM provider, wallet credentials).
4. **Reachability check** — confirm that the target chain RPC and GDEX API are reachable.
5. **Fail closed** — if any step above fails, abort the trade and surface an actionable remediation message to the operator.

---

## Dependency Validation

The following credentials and libraries are required for live trading. The agent must verify they are installed and configured before executing any trade, and fail closed with an actionable remediation message when they are missing.

| Dependency | Source | Remediation |
|-----------|--------|-------------|
| LLM provider credential | `model_list[].api_key` in config or `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / etc. env var | Configure at least one LLM provider in `model_list` or set an env var |
| GDEX trading (optional) | `tools.gdex` in config.json | GDEX uses a shared API key by default; wallets auto-generate on first run |
| Chain RPC endpoint (optional) | Per-chain config or `GCLAW_ETHEREUM_RPC_URL` etc. | Built-in public RPCs are provided for Ethereum, Arbitrum, and Base |

If no LLM provider is configured, the agent **must stop** and print an error such as:

```
RuntimeError: No LLM provider API key is set. Configure model_list in config.json or set at least one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, ...
```

---

## Execution Modes

Gclaw supports two execution modes:

| Mode | Description |
|------|-------------|
| **Dry-run** (default) | Simulates trades without submitting on-chain transactions. No funds at risk. |
| **Live** | Submits real on-chain transactions via GDEX SDK. Requires explicit opt-in. |

### Live Safety Opt-In

Live execution requires **both** of the following:

1. The config flag `execution.live_mode` set to `true` in `config.json`.
2. The explicit CLI confirmation flag `--yes-live` passed at startup.

Config-only gating is **not** sufficient. The `--yes-live` flag is required as an explicit operator approval for live trading.

```bash
# Dry-run (default — no flag needed)
gclaw agent -m "Buy 10 USDC of SOL on Solana"

# Live execution (requires both config + flag)
gclaw agent --yes-live -m "Buy 10 USDC of SOL on Solana"
```

---

## Emergency Exit

To cancel all open orders and liquidate all held inventory in an emergency:

```bash
gclaw agent --unwind-all --yes-live
```

This will:
1. **Cancel all open orders** across all chains.
2. **Market-sell all held positions** to the chain's native stablecoin.
3. **Report** the final portfolio state and any residual balances.

For programmatic use, call `scripts/agent.py --unwind-all --yes-live`.

---

## Critical Notes / Gotchas

1. **Never commit real private keys** — use placeholder values in config.example.json; real keys go in `~/.gclaw/config.json` (gitignored)
2. **GMAC balance is real money** — monitor it; low balance = agent hibernation
3. **Self-replication requires goodwill ≥ 50** — earned through profitable trades and completed tasks
4. **Self-recoding requires goodwill ≥ 100** — agents can modify their own prompts and cron jobs
5. **Swarm mode multiplies costs** — each child agent has its own GMAC budget
6. **Gateway port 18790** — ensure it's not exposed to the public internet without auth
7. **LLM config uses `model_list`** — the `providers` section is deprecated; use `model_list` with `model_name`, `model`, `api_key`, `api_base`
8. **Config file location**: `~/.gclaw/config.json` — `gclaw onboard` creates it on first run
9. **Trading is irreversible** — use dry-run mode to test strategies before going live with `--yes-live`
10. **GDEX uses shared API key by default** — wallets auto-generate on first run; fund the managed wallet shown in `gclaw status`
11. **Venture Architect** (goodwill ≥ 5000) — can create Foundry contract scaffolds with a GMAC buy-and-burn policy
12. **Living Dashboard** — always available at `http://127.0.0.1:18790/dashboard` when running in gateway mode

---

## Source Repository

Full source code, documentation, and issues:
**https://github.com/GemachDAO/Gclaw**

License: MIT
