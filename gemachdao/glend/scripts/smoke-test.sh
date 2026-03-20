#!/usr/bin/env bash
# =============================================================================
# Smoke test for the Glend skill — quick offline validation
# Usage: bash scripts/smoke-test.sh
#
# Validates:
#   1. SKILL.md YAML frontmatter
#   2. Contract addresses are valid hex format
#   3. TypeScript code blocks have matching braces
#   4. Env var names in .env.example match those referenced in SKILL.md
# =============================================================================

set -euo pipefail

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL_MD="$SKILL_ROOT/SKILL.md"

# =============================================================================
# 1. YAML Frontmatter Validation
# =============================================================================
section "1. YAML Frontmatter"

if [[ ! -f "$SKILL_MD" ]]; then
  fail "SKILL.md exists" "File not found: $SKILL_MD"
  printf "\n${RED}Aborting: SKILL.md is required for all other checks.${RESET}\n\n"
  exit 1
fi

SKILL_CONTENT=$(cat "$SKILL_MD")

# Check opening ---
FIRST_LINE=$(head -1 "$SKILL_MD")
if [[ "$FIRST_LINE" == "---" ]]; then
  pass "Frontmatter opens with '---'"
else
  fail "Frontmatter opens with '---'" "First line: '$FIRST_LINE'"
fi

# Count --- delimiters
DELIM_COUNT=$(grep -c '^---$' "$SKILL_MD" || true)
if [[ $DELIM_COUNT -ge 2 ]]; then
  pass "Frontmatter has closing '---' ($DELIM_COUNT delimiters found)"
else
  fail "Frontmatter has closing '---'" "Only $DELIM_COUNT '---' found (need >= 2)"
fi

# Extract frontmatter
FRONTMATTER=$(awk '/^---/{n++; if(n==2){exit}} n==1 && !/^---/' "$SKILL_MD")

# Validate name field
if echo "$FRONTMATTER" | grep -qE '^name:'; then
  NAME_VAL=$(echo "$FRONTMATTER" | grep '^name:' | sed 's/^name:[[:space:]]*//' | tr -d '"'\''')
  pass "name field present: '$NAME_VAL'"

  if [[ "$NAME_VAL" == "glend" ]]; then
    pass "name matches directory 'glend'"
  else
    fail "name matches directory 'glend'" "name is '$NAME_VAL'"
  fi

  if echo "$NAME_VAL" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$'; then
    pass "name format is valid (lowercase, hyphens only, no leading/trailing hyphens)"
  else
    fail "name format is valid" "name '$NAME_VAL' has invalid format"
  fi
else
  fail "name field present in frontmatter" "Missing 'name:'"
fi

# Validate description field
if echo "$FRONTMATTER" | grep -qE '^description:'; then
  DESC_VAL=$(echo "$FRONTMATTER" | grep '^description:' | sed 's/^description:[[:space:]]*//' | tr -d '"')
  DESC_LEN=${#DESC_VAL}
  pass "description field present (${DESC_LEN} chars)"

  if [[ $DESC_LEN -gt 0 && $DESC_LEN -le 1024 ]]; then
    pass "description length valid (<= 1024 chars)"
  else
    fail "description length valid" "length is $DESC_LEN (must be 1-1024)"
  fi
else
  fail "description field present in frontmatter" "Missing 'description:'"
fi

# =============================================================================
# 2. Contract Address Validation
# =============================================================================
section "2. Contract Address Hex Format"

# All contract addresses we expect in the skill
EXPECTED_ADDRESSES=(
  "0xe838eb8011297024bca9c09d4e83e2d3cd74b7d0"
  "0xa8e550710bf113db6a1b38472118b8d6d5176d12"
  "0x2e9d89d372837f71cb529e5ba85bfbc1785c69cd"
  "0x4a4c2A16b58bD63d37e999fDE50C2eBfE3182D58"
  "0x97f602E17ed4e765a6968f295Bdc3F6b4c1Ef93b"
  "0x41d9071C885da8dCa042E05AA66D7D5034383C53"
)

for addr in "${EXPECTED_ADDRESSES[@]}"; do
  # Validate: starts with 0x, followed by exactly 40 hex chars (case-insensitive)
  if echo "$addr" | grep -qiE '^0x[0-9a-f]{40}$'; then
    # Check it's actually in SKILL.md
    if grep -qi "$addr" "$SKILL_MD"; then
      pass "Address $addr is valid hex and present in SKILL.md"
    else
      fail "Address $addr present in SKILL.md" "Address not found in SKILL.md"
    fi
  else
    fail "Address $addr has valid hex format" "Does not match 0x + 40 hex chars"
  fi
done

# =============================================================================
# 3. TypeScript Code Block Brace Balance
# =============================================================================
section "3. TypeScript Code Block Brace Balance"

# Extract all TypeScript code blocks and check brace balance
IN_TS_BLOCK=false
BLOCK_NUM=0
OPEN=0
CLOSE=0
BLOCK_LINES=""

check_balance() {
  local block_num=$1
  local open=$2
  local close=$3
  local snippet=$4
  if [[ $open -eq $close ]]; then
    pass "TypeScript block #${block_num} braces balanced ({: ${open}, }: ${close})"
  else
    fail "TypeScript block #${block_num} braces balanced" \
      "Unbalanced: {=$open, }=$close in: $(echo "$snippet" | head -1)"
  fi
}

while IFS= read -r line; do
  if [[ "$line" =~ ^\`\`\`typescript ]]; then
    IN_TS_BLOCK=true
    BLOCK_NUM=$((BLOCK_NUM + 1))
    OPEN=0
    CLOSE=0
    BLOCK_LINES=""
    continue
  fi
  if [[ "$IN_TS_BLOCK" == "true" && "$line" =~ ^\`\`\`$ ]]; then
    IN_TS_BLOCK=false
    check_balance "$BLOCK_NUM" "$OPEN" "$CLOSE" "$BLOCK_LINES"
    continue
  fi
  if [[ "$IN_TS_BLOCK" == "true" ]]; then
    BLOCK_LINES="$BLOCK_LINES$line\n"
    # Count braces in this line (simple check — doesn't parse string literals or comments;
    # suitable for detecting obvious structural mismatches)
    OPENS=$(echo "$line" | tr -cd '{' | wc -c)
    CLOSES=$(echo "$line" | tr -cd '}' | wc -c)
    OPEN=$((OPEN + OPENS))
    CLOSE=$((CLOSE + CLOSES))
  fi
done < "$SKILL_MD"

if [[ $BLOCK_NUM -eq 0 ]]; then
  fail "TypeScript code blocks found in SKILL.md" "No TypeScript blocks detected"
else
  pass "Found $BLOCK_NUM TypeScript code block(s) in SKILL.md"
fi

# =============================================================================
# 4. Env Var Consistency
# =============================================================================
section "4. Environment Variable Consistency"

ENV_EXAMPLE="$SKILL_ROOT/.env.example"

if [[ ! -f "$ENV_EXAMPLE" ]]; then
  fail ".env.example exists" "Not found: $ENV_EXAMPLE"
else
  # Extract variable names from .env.example (lines like VAR_NAME=...)
  ENV_VARS=$(grep -oE '^[A-Z_][A-Z0-9_]+' "$ENV_EXAMPLE" || true)

  for var in $ENV_VARS; do
    if grep -q "$var" "$SKILL_MD"; then
      pass ".env.example variable '$var' is referenced in SKILL.md"
    else
      fail ".env.example variable '$var' is referenced in SKILL.md" \
        "'$var' from .env.example not found in SKILL.md"
    fi
  done
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
