#!/usr/bin/env bash
# e2e-seren-integration.test.sh — End-to-end integration tests for the Gclaw skill
# Tests run entirely offline — no network requests, no real credentials required.
# Usage: bash scripts/e2e-seren-integration.test.sh
# Exit code 0 = all tests pass, 1 = any failure
set -uo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0

pass()    { echo -e "  ${GREEN}✓${RESET} $1"; PASSED=$((PASSED + 1)); }
fail()    { echo -e "  ${RED}✗${RESET} $1"; [[ -n "${2:-}" ]] && echo -e "    ${RED}→ $2${RESET}"; FAILED=$((FAILED + 1)); }
skip()    { echo -e "  ${YELLOW}⊘${RESET} $1 ${YELLOW}(skipped: ${2:-})${RESET}"; SKIPPED=$((SKIPPED + 1)); }
section() { echo -e "\n${CYAN}▶ $*${RESET}"; }
assert()  {
  local condition="$1"
  local label="$2"
  local detail="${3:-}"
  if eval "$condition" 2>/dev/null; then
    pass "$label"
  else
    fail "$label" "$detail"
  fi
}

# ─── Locate skill root ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SKILL_MD="${SKILL_ROOT}/SKILL.md"

# ═══════════════════════════════════════════════════════════════════════════════
# A. SKILL STRUCTURE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
section "A. Skill Structure Validation"

# A1. Required files exist
assert "[[ -f '${SKILL_MD}' ]]" \
  "SKILL.md exists at ${SKILL_MD}"

assert "[[ -f '${SKILL_ROOT}/README.md' ]]" \
  "README.md exists"

assert "[[ -f '${SKILL_ROOT}/.env.example' ]]" \
  ".env.example exists"

assert "[[ -f '${SKILL_ROOT}/config.example.json' ]]" \
  "config.example.json exists"

assert "[[ -d '${SKILL_ROOT}/scripts' ]]" \
  "scripts/ directory exists"

# A2. Expected script files
for script in install.sh verify.sh smoke-test.sh e2e-seren-integration.test.sh; do
  assert "[[ -f '${SKILL_ROOT}/scripts/${script}' ]]" \
    "scripts/${script} exists"
done

# A3. Scripts are executable or at minimum readable
for script in install.sh verify.sh smoke-test.sh e2e-seren-integration.test.sh; do
  script_path="${SKILL_ROOT}/scripts/${script}"
  if [[ -f "$script_path" ]]; then
    assert "[[ -r '${script_path}' ]]" \
      "scripts/${script} is readable"
  fi
done

# A4. .gitignore exists and covers secrets
assert "[[ -f '${SKILL_ROOT}/.gitignore' ]]" \
  ".gitignore exists"

if [[ -f "${SKILL_ROOT}/.gitignore" ]]; then
  assert "grep -q 'config\.json' '${SKILL_ROOT}/.gitignore'" \
    ".gitignore ignores config.json"
  assert "grep -q '\.env' '${SKILL_ROOT}/.gitignore'" \
    ".gitignore ignores .env"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# B. SKILL.MD FRONTMATTER VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
section "B. SKILL.md Frontmatter Validation"

SKILL_MD_CONTENT=""
if [[ -f "$SKILL_MD" ]]; then
  SKILL_MD_CONTENT="$(cat "$SKILL_MD")"
fi

# B1. Frontmatter delimiters
assert "head -1 '${SKILL_MD}' | grep -q '^---'" \
  "SKILL.md starts with --- (frontmatter delimiter)"

# B2. Extract frontmatter block (between first two ---)
FM_BLOCK=""
if [[ -n "$SKILL_MD_CONTENT" ]]; then
  FM_BLOCK="$(awk '/^---/{if(NR==1){found=1;next}if(found){exit}}found{print}' "$SKILL_MD")"
fi

# B3. Required fields: name
NAME_VALUE=""
if [[ -n "$FM_BLOCK" ]]; then
  NAME_VALUE="$(echo "$FM_BLOCK" | grep '^name:' | head -1 | sed 's/^name:[[:space:]]*//' | tr -d '"'"'")"
fi

assert "[[ -n '${NAME_VALUE}' ]]" \
  "frontmatter has 'name' field" \
  "Add 'name: gclaw-agent' to SKILL.md frontmatter"

# B4. name matches parent directory
PARENT_DIR="$(basename "$SKILL_ROOT")"
assert "[[ '${NAME_VALUE}' == '${PARENT_DIR}' ]]" \
  "name '${NAME_VALUE}' matches parent directory '${PARENT_DIR}'" \
  "name must equal the directory name exactly"

# B5. name spec: 1-64 chars
NAME_LEN="${#NAME_VALUE}"
assert "[[ $NAME_LEN -ge 1 && $NAME_LEN -le 64 ]]" \
  "name length (${NAME_LEN}) is between 1 and 64 chars"

# B6. name spec: lowercase letters, digits, hyphens only
assert "echo '${NAME_VALUE}' | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$'" \
  "name uses only lowercase letters, digits, and hyphens (no leading/trailing hyphen)" \
  "name '${NAME_VALUE}' contains invalid characters"

# B7. name spec: no consecutive hyphens
assert "! echo '${NAME_VALUE}' | grep -q '\-\-'" \
  "name has no consecutive hyphens"

# B8. Required field: description
DESC_VALUE=""
if [[ -n "$FM_BLOCK" ]]; then
  DESC_VALUE="$(echo "$FM_BLOCK" | grep '^description:' | head -1 | sed 's/^description:[[:space:]]*//' | tr -d '"')"
fi

assert "[[ -n '${DESC_VALUE}' ]]" \
  "frontmatter has non-empty 'description' field"

# B9. description <= 1024 chars
DESC_LEN="${#DESC_VALUE}"
assert "[[ $DESC_LEN -le 1024 ]]" \
  "description length (${DESC_LEN}) is <= 1024 chars"

# B10. H1 heading exists in body
assert "grep -q '^# ' '${SKILL_MD}'" \
  "SKILL.md has an H1 heading (display name)"

# ═══════════════════════════════════════════════════════════════════════════════
# C. CONFIGURATION VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
section "C. Configuration Validation"

CONFIG_EXAMPLE="${SKILL_ROOT}/config.example.json"

# C1. config.example.json is valid JSON
if [[ -f "$CONFIG_EXAMPLE" ]]; then
  JSON_VALID=false
  if command -v python3 &>/dev/null; then
    python3 -c "import json; json.load(open('${CONFIG_EXAMPLE}'))" 2>/dev/null && JSON_VALID=true
  elif command -v node &>/dev/null; then
    node -e "JSON.parse(require('fs').readFileSync('${CONFIG_EXAMPLE}','utf8'))" 2>/dev/null && JSON_VALID=true
  fi

  if [[ "$JSON_VALID" == "true" ]]; then
    pass "config.example.json is valid JSON"
  elif command -v python3 &>/dev/null || command -v node &>/dev/null; then
    fail "config.example.json is NOT valid JSON" "Check the file for syntax errors"
  else
    skip "config.example.json JSON validation" "python3 and node not available"
  fi

  # C2. Required top-level keys
  if [[ "$JSON_VALID" == "true" ]] && command -v python3 &>/dev/null; then
    KEYS_CHECK=$(python3 -c "
import json, sys
with open('${CONFIG_EXAMPLE}', 'r') as f:
    d = json.load(f)
missing = [k for k in ['agents', 'model_list'] if k not in d]
if missing:
    print('MISSING:' + ','.join(missing))
    sys.exit(1)
print('OK')
" 2>/dev/null || echo "ERROR")

    if [[ "$KEYS_CHECK" == "OK" ]]; then
      pass "config.example.json has required keys: agents, model_list"
    else
      fail "config.example.json missing required keys" "$KEYS_CHECK"
    fi
  fi
else
  fail "config.example.json does not exist"
fi

# C3. .env.example has expected variable names
ENV_EXAMPLE="${SKILL_ROOT}/.env.example"
if [[ -f "$ENV_EXAMPLE" ]]; then
  for var in OPENAI_API_KEY ANTHROPIC_API_KEY TELEGRAM_BOT_TOKEN; do
    assert "grep -q '${var}' '${ENV_EXAMPLE}'" \
      ".env.example contains ${var} variable"
  done
fi

# C4. config.example.json uses placeholder values (not real secrets)
if [[ -f "$CONFIG_EXAMPLE" ]]; then
  assert "grep -q 'your' '${CONFIG_EXAMPLE}' || grep -q 'YOUR' '${CONFIG_EXAMPLE}'" \
    "config.example.json uses placeholder values for secrets"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# D. SEREN-SKILLS SPEC COMPLIANCE
# ═══════════════════════════════════════════════════════════════════════════════
section "D. Seren-Skills Spec Compliance"

# D1. Directory name is kebab-case
assert "echo '${PARENT_DIR}' | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]+$'" \
  "skill directory name '${PARENT_DIR}' is kebab-case"

# D2. Org directory is lowercase
ORG_DIR="$(basename "$(dirname "$SKILL_ROOT")")"
assert "echo '${ORG_DIR}' | grep -qE '^[a-z0-9][a-z0-9-]*$'" \
  "org directory name '${ORG_DIR}' is lowercase kebab-case"

# D3. Slug would be valid (org-skill-name)
SLUG="${ORG_DIR}-${PARENT_DIR}"
assert "echo '${SLUG}' | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$'" \
  "derived slug '${SLUG}' is valid"

# D4. SKILL.md frontmatter name matches directory (already tested, but verify slug logic)
assert "[[ '${NAME_VALUE}' == 'gclaw-agent' ]]" \
  "name field is exactly 'gclaw-agent' (spec-required value)"

# ═══════════════════════════════════════════════════════════════════════════════
# E. SECURITY — NO SECRETS COMMITTED
# ═══════════════════════════════════════════════════════════════════════════════
section "E. Security — No Secrets Committed"

# E1. No real API keys in any file
# Patterns that indicate a real key (not a placeholder)
SECRET_PATTERNS=(
  'sk-[A-Za-z0-9]{20,}'
  'AKIA[A-Z0-9]{16}'
  'ghp_[A-Za-z0-9]{36}'
  '[0-9]+:[A-Za-z0-9_-]{35,}'
)

FILES_TO_SCAN=(
  "${SKILL_MD}"
  "${SKILL_ROOT}/README.md"
  "${SKILL_ROOT}/.env.example"
  "${SKILL_ROOT}/config.example.json"
  "${SKILL_ROOT}/scripts/install.sh"
  "${SKILL_ROOT}/scripts/verify.sh"
  "${SKILL_ROOT}/scripts/smoke-test.sh"
)

SECRETS_FOUND=false
for f in "${FILES_TO_SCAN[@]}"; do
  [[ -f "$f" ]] || continue
  for pattern in "${SECRET_PATTERNS[@]}"; do
    if grep -qE "$pattern" "$f" 2>/dev/null; then
      fail "Possible secret found in $(basename "$f")" "Pattern: ${pattern}"
      SECRETS_FOUND=true
    fi
  done
done
[[ "$SECRETS_FOUND" == "false" ]] && pass "No hardcoded secrets found in tracked files"

# E2. .env is not tracked (covered by .gitignore)
if [[ -f "${SKILL_ROOT}/.env" ]]; then
  warn_msg=".env file exists locally — ensure it is gitignored"
  if [[ -f "${SKILL_ROOT}/.gitignore" ]] && grep -q '\.env' "${SKILL_ROOT}/.gitignore"; then
    pass ".env is covered by .gitignore"
  else
    fail ".env exists but is not in .gitignore" "$warn_msg"
  fi
else
  pass ".env does not exist in skill directory (correct — use .env.example)"
fi

# E3. config.json (with real keys) is not tracked
if [[ -f "${SKILL_ROOT}/config.json" ]]; then
  if [[ -f "${SKILL_ROOT}/.gitignore" ]] && grep -q 'config\.json' "${SKILL_ROOT}/.gitignore"; then
    pass "config.json is covered by .gitignore"
  else
    fail "config.json exists but is not in .gitignore"
  fi
else
  pass "config.json does not exist in skill directory (correct — use config.example.json)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# F. BINARY VERIFICATION (if gclaw is installed)
# ═══════════════════════════════════════════════════════════════════════════════
section "F. Binary Verification (if gclaw installed)"

if command -v gclaw &>/dev/null; then
  # F1. version exits 0
  gclaw version &>/dev/null && pass "gclaw version exits 0" || fail "gclaw version returned non-zero"

  # F2. help output
  (gclaw --help &>/dev/null 2>&1 || gclaw 2>&1 | grep -qi "usage\|help\|gclaw") \
    && pass "gclaw outputs help text" \
    || warn "gclaw help output not as expected"

  # F3. skills list-builtin
  if gclaw skills list-builtin &>/dev/null 2>&1; then
    pass "gclaw skills list-builtin exits 0"
  else
    skip "gclaw skills list-builtin" "command returned non-zero (may require onboarding)"
  fi

  # F4. Config path resolvable
  GCLAW_HOME_VAL="${GCLAW_HOME:-$HOME/.gclaw}"
  assert "[[ -n '${GCLAW_HOME_VAL}' ]]" \
    "GCLAW_HOME is resolvable: ${GCLAW_HOME_VAL}"
else
  skip "Binary verification" "gclaw not installed — run: bash scripts/install.sh"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# G. DOCKER VERIFICATION (if Docker available)
# ═══════════════════════════════════════════════════════════════════════════════
section "G. Docker Verification (if Docker available)"

if command -v docker &>/dev/null; then
  pass "docker is available: $(docker --version 2>/dev/null | head -1)"

  # Check that SKILL.md documents Docker usage
  assert "grep -qi 'docker' '${SKILL_MD}'" \
    "SKILL.md documents Docker installation"
  assert "grep -q 'docker-compose' '${SKILL_MD}'" \
    "SKILL.md documents docker-compose usage"
else
  skip "Docker checks" "docker not installed"
fi

# Check SKILL.md documents docker-compose.yml pattern regardless
assert "grep -q 'docker-compose' '${SKILL_MD}'" \
  "SKILL.md references docker-compose.yml"

# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
TOTAL=$((PASSED + FAILED + SKIPPED))
echo ""
echo -e "${CYAN}═══ Test Summary ═══${RESET}"
echo -e "  Total:   ${TOTAL}"
echo -e "  ${GREEN}Passed:  ${PASSED}${RESET}"
echo -e "  ${RED}Failed:  ${FAILED}${RESET}"
echo -e "  ${YELLOW}Skipped: ${SKIPPED}${RESET}"
echo ""

if [[ $FAILED -gt 0 ]]; then
  echo -e "${RED}✗ ${FAILED} test(s) failed${RESET}"
  exit 1
else
  echo -e "${GREEN}✓ All tests passed${RESET}"
  [[ $SKIPPED -gt 0 ]] && echo -e "${YELLOW}  (${SKIPPED} test(s) skipped — install gclaw or Docker to run them)${RESET}"
  exit 0
fi
