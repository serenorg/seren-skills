#!/usr/bin/env bash
set -euo pipefail

host="${SEREN_API_HOST:-https://api.serendb.com}"
host="${host%/}"
auto_create="${SEREN_AUTO_CREATE_KEY:-1}"

if [ -n "${SEREN_CREDENTIALS_FILE:-}" ]; then
  cred_file="$SEREN_CREDENTIALS_FILE"
else
  # The seren CLI uses etcetera::choose_base_strategy() which resolves to
  # XDG on all unix platforms (including macOS).  Match that behaviour so
  # the credential scripts and the CLI share the same file.
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      if [ -n "${APPDATA:-}" ]; then
        appdata_posix="${APPDATA//\\//}"
        cred_file="$appdata_posix/seren/credentials.toml"
      else
        cred_file="${XDG_CONFIG_HOME:-$HOME/.config}/seren/credentials.toml"
      fi
      ;;
    *)
      cred_file="${XDG_CONFIG_HOME:-$HOME/.config}/seren/credentials.toml"
      ;;
  esac
fi

SEREN_CREDENTIALS_FILE="$cred_file"

trim() {
  sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

# Read a bare TOML key from the credentials file.
# The CLI writes flat key-value pairs (no section headers):
#   api_key = "..."        (API-key login)
#   access_token = "..."   (OAuth login)
read_toml_key() {
  local key="$1"
  [ -f "$SEREN_CREDENTIALS_FILE" ] || return 1
  sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\"\(.*\)\".*$/\1/p" \
    "$SEREN_CREDENTIALS_FILE" | head -n1
}

read_key_from_json() {
  if command -v jq >/dev/null 2>&1; then
    jq -r '.data.agent.api_key // .api_key // .body.api_key // empty'
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("data",{}).get("agent",{}).get("api_key") or d.get("api_key") or d.get("body",{}).get("api_key", ""))'
    return
  fi

  echo "resolve_credentials.sh requires jq or python3 to parse JSON" >&2
  return 1
}

# Resolve an existing key from the file.  The CLI stores either api_key
# (API-key login) or access_token (OAuth login).  Prefer api_key.
if [ -z "${SEREN_API_KEY:-}" ]; then
  key="$(read_toml_key api_key || true)"
  key="$(printf '%s' "$key" | tr -d '\r\n' | trim)"

  if [ -z "$key" ]; then
    key="$(read_toml_key access_token || true)"
    key="$(printf '%s' "$key" | tr -d '\r\n' | trim)"
  fi

  if [ -n "$key" ]; then
    SEREN_API_KEY="$key"
  fi
fi

if [ -z "${SEREN_API_KEY:-}" ]; then
  if [ "$auto_create" = "0" ]; then
    echo "SEREN_API_KEY not set and no key found in $SEREN_CREDENTIALS_FILE" >&2
    exit 1
  fi

  response="$(curl -fsS -X POST "$host/auth/agent" -H 'Content-Type: application/json' -d '{}')"
  key="$(printf '%s' "$response" | read_key_from_json | tr -d '\r\n' | trim)"
  if [ -z "$key" ]; then
    echo "failed to parse api_key from /auth/agent response" >&2
    exit 1
  fi

  SEREN_API_KEY="$key"

  cred_dir="$(dirname "$SEREN_CREDENTIALS_FILE")"
  mkdir -p "$cred_dir"
  chmod 700 "$cred_dir" 2>/dev/null || true

  # Append to existing file rather than clobbering CLI-written tokens.
  if [ -f "$SEREN_CREDENTIALS_FILE" ] && grep -q '[^[:space:]]' "$SEREN_CREDENTIALS_FILE"; then
    # Remove any existing api_key line, then append the new one.
    sed -i.bak '/^[[:space:]]*api_key[[:space:]]*=/d' "$SEREN_CREDENTIALS_FILE"
    rm -f "${SEREN_CREDENTIALS_FILE}.bak"
    printf 'api_key = "%s"\n' "$SEREN_API_KEY" >> "$SEREN_CREDENTIALS_FILE"
  else
    printf 'api_key = "%s"\n' "$SEREN_API_KEY" > "$SEREN_CREDENTIALS_FILE"
  fi
  chmod 600 "$SEREN_CREDENTIALS_FILE" 2>/dev/null || true
fi

printf 'export SEREN_CREDENTIALS_FILE=%q\n' "$SEREN_CREDENTIALS_FILE"
printf 'export SEREN_API_KEY=%q\n' "$SEREN_API_KEY"
