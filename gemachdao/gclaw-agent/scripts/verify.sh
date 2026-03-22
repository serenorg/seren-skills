#!/usr/bin/env bash
# verify.sh — Offline verification of Gclaw installation
# Usage: bash scripts/verify.sh
# Exit code 0 = all checks pass, 1 = any check fails
set -uo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

PASSED=0
FAILED=0
WARNED=0

pass()  { echo -e "  ${GREEN}✓${RESET} $*"; PASSED=$((PASSED + 1)); }
fail()  { echo -e "  ${RED}✗${RESET} $*"; FAILED=$((FAILED + 1)); }
warn()  { echo -e "  ${YELLOW}⚠${RESET} $*"; WARNED=$((WARNED + 1)); }
section() { echo -e "\n${CYAN}▶ $*${RESET}"; }

GCLAW_HOME="${GCLAW_HOME:-$HOME/.gclaw}"
CONFIG_FILE="${GCLAW_HOME}/config.json"

# ─── A. Binary checks ─────────────────────────────────────────────────────────
section "A. Binary Verification"

if command -v gclaw &>/dev/null; then
  pass "gclaw binary found: $(command -v gclaw)"

  VERSION_OUTPUT=$(gclaw version 2>&1 || true)
  if [[ -n "$VERSION_OUTPUT" ]]; then
    pass "gclaw version: ${VERSION_OUTPUT}"
  else
    warn "gclaw version returned empty output"
  fi

  if gclaw --help &>/dev/null 2>&1 || gclaw 2>&1 | grep -q -i "usage\|help\|gclaw\|command"; then
    pass "gclaw help/usage text is available"
  else
    warn "gclaw did not produce expected help/usage output"
  fi
else
  fail "gclaw binary not found in PATH"
  warn "Run: bash scripts/install.sh   to install Gclaw"
fi

# ─── B. Config file checks ────────────────────────────────────────────────────
section "B. Configuration File"

if [[ -d "$GCLAW_HOME" ]]; then
  pass "GCLAW_HOME directory exists: ${GCLAW_HOME}"
else
  warn "GCLAW_HOME directory not found: ${GCLAW_HOME}"
  warn "Run: gclaw onboard   to initialize workspace"
fi

if [[ -f "$CONFIG_FILE" ]]; then
  pass "config.json exists: ${CONFIG_FILE}"

  # Validate JSON
  if command -v python3 &>/dev/null; then
    if python3 -c "import json, sys; json.load(open('${CONFIG_FILE}'))" 2>/dev/null; then
      pass "config.json is valid JSON"

      # Check required top-level keys
      AGENTS=$(python3 -c "import json; d=json.load(open('${CONFIG_FILE}')); print('ok' if 'agents' in d else 'missing')" 2>/dev/null || echo "error")
      MODEL_LIST=$(python3 -c "import json; d=json.load(open('${CONFIG_FILE}')); print('ok' if 'model_list' in d else 'missing')" 2>/dev/null || echo "error")

      [[ "$AGENTS" == "ok" ]]     && pass "config.json has 'agents' key"     || fail "config.json missing 'agents' key"
      [[ "$MODEL_LIST" == "ok" ]] && pass "config.json has 'model_list' key" || warn "config.json missing 'model_list' key (may be using env vars or providers)"

    else
      fail "config.json is NOT valid JSON — edit ${CONFIG_FILE} to fix syntax errors"
    fi
  elif command -v node &>/dev/null; then
    if node -e "JSON.parse(require('fs').readFileSync('${CONFIG_FILE}','utf8'))" 2>/dev/null; then
      pass "config.json is valid JSON (verified with node)"
    else
      fail "config.json is NOT valid JSON"
    fi
  else
    warn "Neither python3 nor node available — skipping JSON validation"
  fi
else
  warn "config.json not found at ${CONFIG_FILE}"
  warn "Run: gclaw onboard   or copy config.example.json to ${CONFIG_FILE}"
fi

# ─── C. Environment variable checks ──────────────────────────────────────────
section "C. Environment Variables"

check_env() {
  local var="$1"
  local required="${2:-false}"
  if [[ -n "${!var:-}" ]]; then
    # Mask the value for security
    local val="${!var}"
    local masked="${val:0:4}****"
    pass "${var} is set (${masked})"
  else
    if [[ "$required" == "true" ]]; then
      fail "${var} is NOT set (required for DeFi trading)"
    else
      warn "${var} is not set (optional feature may be unavailable)"
    fi
  fi
}

# At least one LLM provider is needed
LLM_SET=false
for var in OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY ZHIPU_API_KEY OPENROUTER_API_KEY CEREBRAS_API_KEY; do
  [[ -n "${!var:-}" ]] && LLM_SET=true
done
[[ "$LLM_SET" == "true" ]] && pass "At least one LLM provider API key is set" || warn "No LLM provider API key is set (may be configured via model_list in config.json)"

check_env "TELEGRAM_BOT_TOKEN"
check_env "DISCORD_BOT_TOKEN"

# ─── D. Skill structure checks ────────────────────────────────────────────────
section "D. Skill Structure"

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "${SKILL_ROOT}/SKILL.md" ]];            then pass "SKILL.md exists";            else fail "SKILL.md is missing"; fi
if [[ -f "${SKILL_ROOT}/README.md" ]];           then pass "README.md exists";           else warn "README.md is missing"; fi
if [[ -f "${SKILL_ROOT}/.env.example" ]];        then pass ".env.example exists";        else warn ".env.example is missing"; fi
if [[ -f "${SKILL_ROOT}/config.example.json" ]]; then pass "config.example.json exists"; else warn "config.example.json is missing"; fi
if [[ -d "${SKILL_ROOT}/scripts" ]];             then pass "scripts/ directory exists";  else fail "scripts/ directory is missing"; fi
if [[ -f "${SKILL_ROOT}/scripts/install.sh" ]];  then pass "scripts/install.sh exists";  else warn "scripts/install.sh is missing"; fi

# Validate config.example.json is valid JSON
if [[ -f "${SKILL_ROOT}/config.example.json" ]]; then
  if command -v python3 &>/dev/null; then
    if python3 -c "import json; json.load(open('${SKILL_ROOT}/config.example.json'))" 2>/dev/null; then
      pass "config.example.json is valid JSON"
    else
      fail "config.example.json is NOT valid JSON"
    fi
  fi
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}═══ Verification Summary ═══${RESET}"
echo -e "  ${GREEN}Passed:${RESET}  ${PASSED}"
echo -e "  ${YELLOW}Warnings:${RESET} ${WARNED}"
echo -e "  ${RED}Failed:${RESET}  ${FAILED}"
echo ""

if [[ $FAILED -gt 0 ]]; then
  echo -e "${RED}✗ Verification failed — address the failures above before running the agent${RESET}"
  exit 1
else
  echo -e "${GREEN}✓ Verification passed${RESET}"
  [[ $WARNED -gt 0 ]] && echo -e "${YELLOW}  (${WARNED} warnings — some optional features may be unavailable)${RESET}"
  exit 0
fi
