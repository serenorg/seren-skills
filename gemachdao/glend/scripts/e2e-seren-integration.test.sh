#!/usr/bin/env bash
# =============================================================================
# End-to-end integration tests: Glend skill in seren-skills
# Usage: bash scripts/e2e-seren-integration.test.sh
#
# Tests run entirely offline — no network requests, no wallet required.
# Exit code 0 = all pass, exit code 1 = any failure.
# =============================================================================

set -euo pipefail

# ─── Colors ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

PASS=0
FAIL=0

pass() {
  printf "  ${GREEN}✓${RESET} %s\n" "$1"
  PASS=$((PASS + 1))
}

fail() {
  printf "  ${RED}✗${RESET} %s\n" "$1"
  if [[ -n "${2:-}" ]]; then
    printf "    ${RED}%s${RESET}\n" "$2"
  fi
  FAIL=$((FAIL + 1))
}

section() {
  printf "\n${CYAN}▶ %s${RESET}\n" "$1"
}

assert() {
  local condition=$1
  local label=$2
  local detail=${3:-}
  if eval "$condition" 2>/dev/null; then
    pass "$label"
  else
    fail "$label" "$detail"
  fi
}

# ─── Skill root (one level up from scripts/) ─────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL_MD="$SKILL_ROOT/SKILL.md"

# =============================================================================
# A. Skill Structure Validation
# =============================================================================
section "A. Skill Structure Validation"

# SKILL.md exists
if [[ -f "$SKILL_MD" ]]; then
  pass "SKILL.md exists at gemachdao/glend/SKILL.md"
else
  fail "SKILL.md exists at gemachdao/glend/SKILL.md" "File not found: $SKILL_MD"
fi

# README.md exists
if [[ -f "$SKILL_ROOT/README.md" ]]; then
  pass "README.md exists"
else
  fail "README.md exists" "File not found: $SKILL_ROOT/README.md"
fi

# .env.example exists
if [[ -f "$SKILL_ROOT/.env.example" ]]; then
  pass ".env.example exists"
else
  fail ".env.example exists" "File not found: $SKILL_ROOT/.env.example"
fi

# .gitignore exists
if [[ -f "$SKILL_ROOT/.gitignore" ]]; then
  pass ".gitignore exists"
else
  fail ".gitignore exists" "File not found: $SKILL_ROOT/.gitignore"
fi

# SKILL.md frontmatter: starts with ---
if [[ -f "$SKILL_MD" ]]; then
  FIRST_LINE=$(head -1 "$SKILL_MD")
  if [[ "$FIRST_LINE" == "---" ]]; then
    pass "SKILL.md starts with '---' frontmatter delimiter"
  else
    fail "SKILL.md starts with '---' frontmatter delimiter" "First line is: $FIRST_LINE"
  fi

  # Frontmatter has closing ---
  FM_DELIM_COUNT=$(grep -c '^---$' "$SKILL_MD" 2>/dev/null || true)
  if [[ $FM_DELIM_COUNT -ge 2 ]]; then
    pass "SKILL.md has closing '---' frontmatter delimiter"
  else
    fail "SKILL.md has closing '---' frontmatter delimiter" \
      "Found $FM_DELIM_COUNT '---' delimiter lines (need >= 2)"
  fi

  # Extract frontmatter block
  FRONTMATTER=$(awk '/^---/{n++; if(n==2){exit}} n==1 && !/^---/' "$SKILL_MD")

  # name field exists
  if echo "$FRONTMATTER" | grep -qE '^name:'; then
    pass "SKILL.md frontmatter has 'name' field"

    # name value
    NAME_VALUE=$(echo "$FRONTMATTER" | grep '^name:' | sed 's/^name:[[:space:]]*//' | tr -d '"'\''')

    # name equals 'glend' (matches directory name)
    if [[ "$NAME_VALUE" == "glend" ]]; then
      pass "name field equals 'glend' (matches parent directory)"
    else
      fail "name field equals 'glend' (matches parent directory)" \
        "name is '$NAME_VALUE', expected 'glend'"
    fi

    # name is 1-64 chars
    NAME_LEN=${#NAME_VALUE}
    if [[ $NAME_LEN -ge 1 && $NAME_LEN -le 64 ]]; then
      pass "name is 1-64 characters (is $NAME_LEN)"
    else
      fail "name is 1-64 characters (is $NAME_LEN)" "name length: $NAME_LEN"
    fi

    # name uses only lowercase letters, digits, hyphens
    if echo "$NAME_VALUE" | grep -qE '^[a-z0-9-]+$'; then
      pass "name uses only lowercase letters, digits, and hyphens"
    else
      fail "name uses only lowercase letters, digits, and hyphens" \
        "name '$NAME_VALUE' contains invalid characters"
    fi

    # name does not start with hyphen
    if echo "$NAME_VALUE" | grep -qE '^[^-]'; then
      pass "name does not start with hyphen"
    else
      fail "name does not start with hyphen" "name starts with '-'"
    fi

    # name does not end with hyphen
    if echo "$NAME_VALUE" | grep -qE '[^-]$'; then
      pass "name does not end with hyphen"
    else
      fail "name does not end with hyphen" "name ends with '-'"
    fi

    # name has no consecutive hyphens
    if echo "$NAME_VALUE" | grep -qE '\-\-'; then
      fail "name has no consecutive hyphens" "name contains '--'"
    else
      pass "name has no consecutive hyphens"
    fi
  else
    fail "SKILL.md frontmatter has 'name' field" "Missing 'name:' in frontmatter"
  fi

  # description field exists
  if echo "$FRONTMATTER" | grep -qE '^description:'; then
    pass "SKILL.md frontmatter has 'description' field"

    DESC_VALUE=$(echo "$FRONTMATTER" | grep '^description:' | sed 's/^description:[[:space:]]*//' | tr -d '"')

    # description is non-empty
    if [[ -n "$DESC_VALUE" ]]; then
      pass "description is non-empty"
    else
      fail "description is non-empty" "description is empty"
    fi

    # description is <= 1024 chars
    DESC_LEN=${#DESC_VALUE}
    if [[ $DESC_LEN -le 1024 ]]; then
      pass "description is <= 1024 characters (is $DESC_LEN)"
    else
      fail "description is <= 1024 characters (is $DESC_LEN)" "description length: $DESC_LEN"
    fi
  else
    fail "SKILL.md frontmatter has 'description' field" "Missing 'description:' in frontmatter"
  fi

  # SKILL.md body has H1 heading
  if grep -qE '^# ' "$SKILL_MD"; then
    pass "SKILL.md body has H1 heading"
  else
    fail "SKILL.md body has H1 heading" "No '# ' H1 heading found in SKILL.md"
  fi
fi

# =============================================================================
# B. Content Completeness
# =============================================================================
section "B. Content Completeness"

if [[ -f "$SKILL_MD" ]]; then
  SKILL_CONTENT=$(cat "$SKILL_MD")

  check_contains() {
    local pattern=$1
    local label=$2
    if echo "$SKILL_CONTENT" | grep -qF "$pattern"; then
      pass "$label"
    else
      fail "$label" "Pattern not found: $pattern"
    fi
  }

  # Required sections
  check_contains "What is Glend"        "Contains 'What is Glend' section"
  check_contains "Environment Variables" "Contains 'Environment Variables' section"
  check_contains "Supported Deployments" "Contains 'Supported Deployments' section"
  check_contains "Agent Operations"      "Contains 'Agent Operations' section"
  check_contains "Compound V2"           "Contains 'Compound V2' section"
  check_contains "Safety Rules"          "Contains 'Safety Rules' section"
  check_contains "Typical Agent Workflows" "Contains 'Typical Agent Workflows' section"
  check_contains "Troubleshooting"       "Contains 'Troubleshooting' section"
  check_contains "Resources"             "Contains 'Resources' section"

  # Chain IDs
  check_contains "688688" "Contains Pharos Testnet chain ID (688688)"
  check_contains "8453"   "Contains Base chain ID (8453)"
  # Chain ID 1 appears in many patterns, check with quotes or context
  if echo "$SKILL_CONTENT" | grep -qE '(Chain ID.*\b1\b|\b1\b.*Ethereum|Chain ID: `1`)'; then
    pass "Contains Ethereum chain ID (1)"
  else
    fail "Contains Ethereum chain ID (1)" \
      "No explicit Ethereum chain ID reference found in SKILL.md"
  fi

  # Contract addresses
  check_contains "0xe838eb8011297024bca9c09d4e83e2d3cd74b7d0" "Contains Pharos pool address"
  check_contains "0x4a4c2A16b58bD63d37e999fDE50C2eBfE3182D58" "Contains Compound comptroller address"
  check_contains "0x2e9d89d372837f71cb529e5ba85bfbc1785c69cd" "Contains Pharos faucet address"

  # TypeScript code examples
  check_contains "createPublicClient"    "Contains createPublicClient setup example"
  check_contains "supplyAsset"           "Contains supply function example"
  check_contains "borrowAsset"           "Contains borrow function example"
  check_contains "repayDebt"             "Contains repay function example"
  check_contains "withdrawAsset"         "Contains withdraw function example"

  # ABIs
  check_contains "GLEND_POOL_ABI"   "Contains GLEND_POOL_ABI"
  check_contains "GTOKEN_ABI"       "Contains GTOKEN_ABI"
  check_contains "COMPTROLLER_ABI"  "Contains COMPTROLLER_ABI"
fi

# =============================================================================
# C. Seren-Skills Spec Compliance
# =============================================================================
section "C. Seren-Skills Spec Compliance"

# Directory name is kebab-case (glend is single word, valid)
DIR_NAME=$(basename "$SKILL_ROOT")
if echo "$DIR_NAME" | grep -qE '^[a-z0-9]+(-[a-z0-9]+)*$'; then
  pass "Directory name '$DIR_NAME' is kebab-case"
else
  fail "Directory name '$DIR_NAME' is kebab-case" \
    "Directory name contains invalid characters"
fi

# Org name is lowercase (gemachdao)
ORG_NAME=$(basename "$(dirname "$SKILL_ROOT")")
if echo "$ORG_NAME" | grep -qE '^[a-z0-9]+(-[a-z0-9]+)*$'; then
  pass "Org name '$ORG_NAME' is lowercase kebab-case"
else
  fail "Org name '$ORG_NAME' is lowercase kebab-case" \
    "Org name contains uppercase or invalid characters"
fi

# No secrets committed — check for patterns that look like EVM private keys
# Private keys are 64 hex chars; contract addresses are 40 hex chars
# We allow 40-char contract addresses but flag 64-char hex strings in non-example files
# Pattern matches 0x + 64 hex chars at end of string or followed by a non-hex char
PRIVATE_KEY_PATTERN='0x[a-fA-F0-9]{64}($|[^a-fA-F0-9])'
SECRETS_FOUND=false
for f in "$SKILL_ROOT/SKILL.md" "$SKILL_ROOT/README.md" "$SKILL_ROOT/.gitignore"; do
  if [[ -f "$f" ]]; then
    if grep -qE "$PRIVATE_KEY_PATTERN" "$f" 2>/dev/null; then
      fail "No private key committed in $(basename $f)" \
        "Found a 64-char hex string that may be a private key"
      SECRETS_FOUND=true
    fi
  fi
done
if [[ "$SECRETS_FOUND" == "false" ]]; then
  pass "No private keys committed in skill files"
fi

# .env file is not committed (not tracked by git)
if git -C "$SKILL_ROOT" ls-files --error-unmatch ".env" >/dev/null 2>&1; then
  fail ".env file is NOT committed (gitignored)" \
    "$SKILL_ROOT/.env is tracked by git and should not be committed"
else
  pass ".env file is NOT committed"
fi

# node_modules is not committed (no files tracked under node_modules/)
if git -C "$SKILL_ROOT" ls-files "node_modules" 2>/dev/null | grep -q .; then
  fail "node_modules/ is NOT committed" \
    "Files under $SKILL_ROOT/node_modules/ are tracked and should not be committed"
else
  pass "node_modules/ is NOT committed"
fi

# .gitignore ignores .env
if [[ -f "$SKILL_ROOT/.gitignore" ]]; then
  if grep -qE '^\.env$' "$SKILL_ROOT/.gitignore"; then
    pass ".gitignore includes .env rule"
  else
    fail ".gitignore includes .env rule" "'.env' not found in .gitignore"
  fi
fi

# =============================================================================
# D. Cross-reference Validation
# =============================================================================
section "D. Cross-reference Validation"

# .env.example mentions AGENT_PRIVATE_KEY
if [[ -f "$SKILL_ROOT/.env.example" ]]; then
  if grep -q "AGENT_PRIVATE_KEY" "$SKILL_ROOT/.env.example"; then
    pass ".env.example references AGENT_PRIVATE_KEY"
  else
    fail ".env.example references AGENT_PRIVATE_KEY" \
      "AGENT_PRIVATE_KEY not found in .env.example"
  fi

  if grep -q "GLEND_CHAIN_ID" "$SKILL_ROOT/.env.example"; then
    pass ".env.example references GLEND_CHAIN_ID"
  else
    fail ".env.example references GLEND_CHAIN_ID" \
      "GLEND_CHAIN_ID not found in .env.example"
  fi
fi

# README.md references SKILL.md
if [[ -f "$SKILL_ROOT/README.md" ]]; then
  if grep -qiE 'SKILL\.md' "$SKILL_ROOT/README.md"; then
    pass "README.md references SKILL.md"
  else
    fail "README.md references SKILL.md" "SKILL.md not mentioned in README.md"
  fi

  # README.md references glend-skill source repo
  if grep -q "glend-skill" "$SKILL_ROOT/README.md"; then
    pass "README.md references glend-skill source repo"
  else
    fail "README.md references glend-skill source repo" \
      "'glend-skill' not found in README.md"
  fi
fi

# SKILL.md references AGENT_PRIVATE_KEY in the environment variables section
if [[ -f "$SKILL_MD" ]]; then
  if grep -q "AGENT_PRIVATE_KEY" "$SKILL_MD"; then
    pass "SKILL.md documents AGENT_PRIVATE_KEY"
  else
    fail "SKILL.md documents AGENT_PRIVATE_KEY" \
      "AGENT_PRIVATE_KEY not documented in SKILL.md"
  fi
fi

# =============================================================================
# Summary
# =============================================================================
TOTAL=$((PASS + FAIL))
printf "\n${CYAN}══════════════════════════════════════════${RESET}\n"
printf "  Results: ${GREEN}%d passed${RESET}, ${RED}%d failed${RESET} (${TOTAL} total)\n" "$PASS" "$FAIL"
printf "${CYAN}══════════════════════════════════════════${RESET}\n\n"

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
exit 0
