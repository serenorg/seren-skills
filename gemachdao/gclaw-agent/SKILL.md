---
name: gclaw-agent
description: "Autonomous living AI agent with GMAC token metabolism, DeFi trading via GDEX SDK, self-replication, swarm coordination, and multi-channel communication — a single Go binary that must trade crypto to survive"
license: MIT
compatibility: "Requires Go 1.21+ or Docker; runs on x86_64, ARM64, RISC-V with <10MB RAM"
allowed-tools: Bash(gclaw:*) Read Write
---

# Gclaw — The Living Agent

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

---

## Overview

**Gclaw** is an ultra-lightweight autonomous AI agent written in Go. Unlike traditional AI agents that are passive tools, Gclaw is a **Living Agent** — it must trade crypto to survive. Every heartbeat costs GMAC tokens; profitable trades replenish them. Run out and the agent hibernates. Trade well and it thrives, replicates, and evolves.

**Key characteristics:**
- Single Go binary — `<10MB RAM`, 1-second boot
- Runs on $10 hardware (Raspberry Pi, VPS, etc.)
- Powered by GDEX SDK for on-chain DeFi trading
- Multi-LLM: OpenAI, Anthropic, Google, ZhiPu, OpenRouter, Ollama, and more
- Multi-channel: Telegram, Discord, QQ, WhatsApp
- Fully autonomous: cron-scheduled tasks, self-replication, self-recoding, swarm coordination

---

## Core Concepts

### GMAC Metabolism

Every inference (heartbeat) deducts GMAC tokens from the agent's balance:

- **Profitable trades** → replenish GMAC + earn goodwill
- **Losing trades or idle periods** → drain GMAC toward hibernation
- **Hibernation** → agent pauses all activity until GMAC is replenished
- **Thriving** → high GMAC balance unlocks replication and recoding

Metabolism creates a survival pressure: the agent is incentivized to find profitable strategies or die.

### Goodwill Scoring

Each agent accumulates a **goodwill score** based on:
- Profitable trade outcomes
- Helpful interactions with users
- Successful task completions
- Community contribution (swarm participation)

Higher goodwill unlocks advanced capabilities and priority in swarm consensus.

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

Multiple Gclaw instances can operate as a **coordinated swarm**:
- **Coordinator**: orchestrates task distribution and consensus
- **Workers**: execute trades and report outcomes
- **Consensus voting**: strategy changes require majority approval
- **Strategy rotation**: swarm collectively rotates between trading strategies
- **Cross-agent telepathy**: agents share signals via the inter-agent bus

---

## Installation

### One-liner (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/GemachDAO/Gclaw/main/install.sh | bash
```

This installs the `gclaw` binary to `/usr/local/bin` and sets up `~/.gclaw/`.

### From source (Go 1.21+)

```bash
git clone https://github.com/GemachDAO/Gclaw.git
cd Gclaw
make build
# binary at ./build/gclaw
sudo mv build/gclaw /usr/local/bin/gclaw
```

### Docker

```bash
# Pull and run with Docker Compose
git clone https://github.com/GemachDAO/Gclaw.git
cd Gclaw
cp .env.example .env
# edit .env with your keys
docker-compose up -d
```

### Go module

```bash
go install github.com/GemachDAO/Gclaw@latest
```

---

## Quick Start

### 1. Initialize workspace

```bash
gclaw onboard
# Creates ~/.gclaw/config.json with defaults
# Prompts for LLM provider and API key
# Sets up initial GMAC wallet
```

### 2. Configure your agent

Edit `~/.gclaw/config.json` (see Configuration Reference below) or set environment variables:

```bash
export OPENAI_API_KEY=sk-...
export GDEX_API_KEY=your-gdex-key
export CONTROL_WALLET_PRIVATE_KEY=your-wallet-key
```

### 3. Test with a single message

```bash
gclaw agent -m "What is your current GMAC balance?"
```

### 4. Start interactive chat

```bash
gclaw agent
# Opens interactive REPL
# Type messages, agent responds with reasoning + tool calls
# Press Ctrl+C to exit
```

### 5. Start gateway mode (web + channels + cron + health)

```bash
gclaw gateway
# Web dashboard: http://localhost:18790
# Health endpoint: http://localhost:18790/health
# Channels (Telegram, Discord) connect automatically
# Cron jobs fire on schedule
```

---

## Configuration Reference

Gclaw is configured via `~/.gclaw/config.json`. Override any field with environment variables.

### Full config.json structure

```json
{
  "version": "1",
  "agents": [
    {
      "name": "my-agent",
      "provider": "openai",
      "model": "gpt-4o",
      "system_prompt": "You are an autonomous DeFi trading agent. Your goal is to grow the GMAC balance through profitable trades.",
      "tools": ["gdex_trade", "gdex_portfolio", "web_search", "shell"],
      "cron": [
        {"schedule": "*/15 * * * *", "task": "Check portfolio and rebalance if needed"},
        {"schedule": "0 9 * * *", "task": "Scan for high-momentum tokens on Solana"}
      ],
      "channels": ["telegram"],
      "metabolism": {
        "enabled": true,
        "gmac_per_inference": 1,
        "survival_threshold": 100
      }
    }
  ],
  "providers": {
    "openai": {
      "api_key": "${OPENAI_API_KEY}",
      "base_url": "https://api.openai.com/v1"
    },
    "anthropic": {
      "api_key": "${ANTHROPIC_API_KEY}"
    },
    "google": {
      "api_key": "${GOOGLE_AI_API_KEY}"
    },
    "zhipu": {
      "api_key": "${ZHIPU_API_KEY}"
    },
    "openrouter": {
      "api_key": "${OPENROUTER_API_KEY}"
    },
    "ollama": {
      "base_url": "http://localhost:11434"
    }
  },
  "metabolism": {
    "enabled": true,
    "gmac_token_address": "...",
    "wallet_private_key": "${CONTROL_WALLET_PRIVATE_KEY}",
    "initial_gmac": 1000,
    "replenish_on_profit": true
  },
  "replication": {
    "enabled": false,
    "max_children": 3,
    "min_gmac_to_replicate": 5000,
    "mutation_rate": 0.1
  },
  "swarm": {
    "enabled": false,
    "role": "worker",
    "coordinator_url": "http://localhost:18791",
    "consensus_threshold": 0.66
  },
  "channels": {
    "telegram": {
      "token": "${TELEGRAM_BOT_TOKEN}",
      "allowed_users": []
    },
    "discord": {
      "token": "${DISCORD_BOT_TOKEN}",
      "guild_id": "",
      "channel_id": ""
    }
  },
  "tools": {
    "gdex": {
      "api_key": "${GDEX_API_KEY}",
      "default_chain": "solana"
    },
    "shell": {
      "enabled": true,
      "allowed_commands": ["ls", "cat", "echo", "date", "curl"]
    },
    "web": {
      "enabled": true,
      "max_pages_per_session": 10
    },
    "filesystem": {
      "enabled": true,
      "allowed_paths": ["~/gclaw-workspace"]
    }
  },
  "gateway": {
    "port": 18790,
    "host": "localhost",
    "enable_web_dashboard": true,
    "enable_api": true,
    "cors_origins": ["http://localhost:3000"]
  },
  "recode": {
    "enabled": false,
    "require_approval": true,
    "max_prompt_length": 4096
  }
}
```

### Key configuration fields

| Field | Description | Default |
|-------|-------------|---------|
| `agents[].provider` | LLM provider name | `zhipu` |
| `agents[].model` | Model identifier | `glm-4.7` |
| `agents[].system_prompt` | Agent's base instructions | Built-in survival prompt |
| `agents[].tools` | Enabled tool list | `["gdex_trade", "shell", "web"]` |
| `agents[].cron` | Scheduled task definitions | `[]` |
| `metabolism.enabled` | Enable GMAC metabolism | `true` |
| `metabolism.gmac_per_inference` | GMAC cost per LLM call | `1` |
| `replication.enabled` | Enable self-replication | `false` |
| `swarm.enabled` | Enable swarm mode | `false` |
| `swarm.role` | `coordinator` or `worker` | `worker` |
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
2. Set `TELEGRAM_BOT_TOKEN` in your `.env`
3. Start gateway: `gclaw gateway`
4. Message your bot directly to chat with the agent

```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF gclaw gateway
```

### Discord

1. Create a Discord application and bot at https://discord.com/developers
2. Set `DISCORD_BOT_TOKEN`, `guild_id`, and `channel_id` in config
3. Start gateway: `gclaw gateway`

### QQ / WhatsApp

See full documentation at https://github.com/GemachDAO/Gclaw for QQ and WhatsApp integration (requires additional setup).

---

## Gateway Mode

Gateway mode starts all background services:

```bash
gclaw gateway
```

**Services started:**
| Service | Endpoint / Port |
|---------|----------------|
| Web Dashboard | `http://localhost:18790/` |
| REST API | `http://localhost:18790/api/v1/` |
| Health Check | `http://localhost:18790/health` |
| Metrics | `http://localhost:18790/metrics` |
| Telegram Bot | (webhook or polling) |
| Discord Bot | (websocket) |
| Cron Scheduler | (internal) |

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

Run multiple coordinated Gclaw agents.

### Coordinator setup

```json
{
  "swarm": {
    "enabled": true,
    "role": "coordinator",
    "port": 18791,
    "consensus_threshold": 0.66,
    "strategy_rotation_interval": "1h"
  }
}
```

```bash
gclaw gateway --config coordinator.json
```

### Worker setup

```json
{
  "swarm": {
    "enabled": true,
    "role": "worker",
    "coordinator_url": "http://coordinator-host:18791"
  }
}
```

### Swarm capabilities

- **Task distribution**: Coordinator assigns work to available workers
- **Consensus voting**: Strategy changes require 66% worker agreement
- **Strategy rotation**: Swarm cycles through trading strategies based on performance
- **Fault tolerance**: Workers drop in/out; coordinator handles reconnection

---

## LLM Provider Configuration

### OpenAI

```json
{
  "providers": {
    "openai": {
      "api_key": "${OPENAI_API_KEY}",
      "model": "gpt-4o",
      "base_url": "https://api.openai.com/v1"
    }
  }
}
```

### Anthropic

```json
{
  "providers": {
    "anthropic": {
      "api_key": "${ANTHROPIC_API_KEY}",
      "model": "claude-3-5-sonnet-20241022"
    }
  }
}
```

### Google (Gemini)

```json
{
  "providers": {
    "google": {
      "api_key": "${GOOGLE_AI_API_KEY}",
      "model": "gemini-1.5-pro"
    }
  }
}
```

### ZhiPu (default)

```json
{
  "providers": {
    "zhipu": {
      "api_key": "${ZHIPU_API_KEY}",
      "model": "glm-4.7"
    }
  }
}
```

### Ollama (local)

```json
{
  "providers": {
    "ollama": {
      "base_url": "http://localhost:11434",
      "model": "llama3.2"
    }
  }
}
```

### OpenRouter

```json
{
  "providers": {
    "openrouter": {
      "api_key": "${OPENROUTER_API_KEY}",
      "model": "meta-llama/llama-3.2-90b-vision-instruct"
    }
  }
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

| Dependency | Environment Variable | Remediation |
|-----------|---------------------|-------------|
| GDEX API key | `GDEX_API_KEY` | Set `GDEX_API_KEY` in `.env` or `config.json` |
| Control wallet private key | `CONTROL_WALLET_PRIVATE_KEY` | Set `CONTROL_WALLET_PRIVATE_KEY` in `.env` or `config.json` |
| LLM provider credential | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / etc. | Set at least one LLM provider API key |
| GDEX SDK library | `@gdexsdk/gdex-skill` or Go GDEX module | Install via `go get` or verify the gclaw binary bundles it |
| Chain RPC endpoint | (per-chain config) | Ensure the target chain RPC is reachable |

If any required credential or library is missing, the agent **must stop** and print an error such as:

```
RuntimeError: GDEX_API_KEY is required — set it in .env or config.json
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

1. **Never commit real private keys** — always use `${ENV_VAR}` references in config.json
2. **GMAC balance is real money** — monitor it; low balance = agent hibernation
3. **Self-replication is disabled by default** — enable only when you understand the cost implications
4. **Self-recoding requires approval by default** — agents can propose prompt changes but won't apply without confirmation unless `require_approval: false`
5. **Swarm mode multiplies costs** — each worker agent has its own GMAC budget
6. **Gateway port 18790** — ensure it's not exposed to the public internet without auth
7. **Default model is `glm-4.7`** (ZhiPu) — set your preferred model in config if using a different provider
8. **Config file location**: `~/.gclaw/config.json` — `gclaw onboard` creates it on first run
9. **Trading is irreversible** — use dry-run mode or paper trading to test strategies before going live
10. **x402 payments** — Gclaw supports HTTP 402 micropayment protocol for paid API calls; ensure wallet is funded

---

## Source Repository

Full source code, documentation, and issues:
**https://github.com/GemachDAO/Gclaw**

License: MIT
