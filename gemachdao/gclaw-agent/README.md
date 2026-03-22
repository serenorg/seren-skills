# Gclaw Agent Skill

This skill integrates **Gclaw — The Living Agent** into Seren Desktop, giving you an autonomous AI agent that trades DeFi to survive.

## What This Skill Provides

- Full installation and setup guidance for the Gclaw binary
- Configuration templates for LLM providers (`model_list`), DeFi trading, and channels
- Scripts for installation, verification, and testing
- Integration with the seren-skills ecosystem

## Source Repository

**https://github.com/GemachDAO/Gclaw**

Gclaw is an ultra-lightweight autonomous AI agent written in Go. It runs on `<10MB RAM`, boots in 1 second, and uses GMAC token metabolism — it must trade crypto to survive.

## Quick Start

```bash
# 1. Install Gclaw (one-liner)
curl -fsSL https://raw.githubusercontent.com/GemachDAO/Gclaw/main/install.sh | bash

# Or install via the skill script:
bash scripts/install.sh

# 2. Run the interactive setup wizard
gclaw onboard

# 3. Start interactive agent
gclaw agent

# 4. Or start as a long-running gateway (web dashboard, channels, cron)
gclaw gateway
```

The setup wizard creates `~/.gclaw/config.json` with your chosen LLM provider pre-configured. Living Agent features (GMAC metabolism, GDEX trading, dashboard) are enabled by default.

## Directory Layout

```
gemachdao/gclaw-agent/
├── SKILL.md                          # Full skill documentation (read this!)
├── README.md                         # This file
├── .env.example                      # Environment variable template
├── config.example.json               # Config.json template
├── .gitignore                        # Ignores config.json, .env, logs
└── scripts/
    ├── install.sh                    # Install Gclaw binary
    ├── agent.py                      # Agent launcher with safety guardrails
    ├── verify.sh                     # Verify installation
    ├── smoke-test.sh                 # Quick live smoke test
    └── e2e-seren-integration.test.sh # Full E2E test suite
```

## License

MIT — see https://github.com/GemachDAO/Gclaw/blob/main/LICENSE
