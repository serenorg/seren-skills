#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/seren_api.sh <method> <path-or-url> [options] [-- <extra-curl-args>]

Examples:
  scripts/seren_api.sh get /wallet/balance
  scripts/seren_api.sh post /wallet/recovery --data '{}'
  scripts/seren_api.sh get /openapi.json --no-auth -- -o /dev/null -w '%{http_code}\n'

Options:
  --no-auth                 Skip credential resolution and auth header.
  --host <url>              API host (default: SEREN_API_HOST or https://api.serendb.com).
  -H, --header <header>     Extra HTTP header (repeatable).
  -d, --data <json>         Inline request body.
  --data-file <path>        Request body file.
  -h, --help                Show help.
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ "$#" -lt 2 ]; then
  usage >&2
  exit 2
fi

method="$1"
path_or_url="$2"
shift 2

host="${SEREN_API_HOST:-https://api.serendb.com}"
host="${host%/}"
no_auth=0
data=""
data_file=""

declare -a headers=()
declare -a extra_curl_args=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-auth)
      no_auth=1
      shift
      ;;
    --host)
      [ "$#" -ge 2 ] || { echo "--host requires a value" >&2; exit 2; }
      host="$2"
      host="${host%/}"
      shift 2
      ;;
    -H|--header)
      [ "$#" -ge 2 ] || { echo "$1 requires a value" >&2; exit 2; }
      headers+=("$2")
      shift 2
      ;;
    -d|--data|--json)
      [ "$#" -ge 2 ] || { echo "$1 requires a value" >&2; exit 2; }
      data="$2"
      shift 2
      ;;
    --data-file)
      [ "$#" -ge 2 ] || { echo "--data-file requires a value" >&2; exit 2; }
      data_file="$2"
      shift 2
      ;;
    --)
      shift
      if [ "$#" -gt 0 ]; then
        extra_curl_args=("$@")
      fi
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -n "$data" ] && [ -n "$data_file" ]; then
  echo "Use either --data or --data-file, not both." >&2
  exit 2
fi

if [ "$no_auth" -eq 0 ]; then
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck disable=SC1090
  eval "$("$script_dir/resolve_credentials.sh")"

  if [ -z "${SEREN_API_KEY:-}" ]; then
    echo "Failed to resolve SEREN_API_KEY." >&2
    exit 1
  fi

  headers+=("Authorization: Bearer $SEREN_API_KEY")
fi

has_content_type=0
for h in ${headers[@]+"${headers[@]}"}; do
  if printf '%s' "$h" | grep -qiE '^[[:space:]]*content-type[[:space:]]*:'; then
    has_content_type=1
    break
  fi
done

if { [ -n "$data" ] || [ -n "$data_file" ]; } && [ "$has_content_type" -eq 0 ]; then
  headers+=("Content-Type: application/json")
fi

if [[ "$path_or_url" =~ ^https?:// ]]; then
  url="$path_or_url"
else
  if [[ "$path_or_url" = /* ]]; then
    url="$host$path_or_url"
  else
    url="$host/$path_or_url"
  fi
fi

method_upper="$(printf '%s' "$method" | tr '[:lower:]' '[:upper:]')"

declare -a curl_args=(-sS -X "$method_upper")
for h in ${headers[@]+"${headers[@]}"}; do
  curl_args+=(-H "$h")
done

if [ -n "$data_file" ]; then
  if [ ! -f "$data_file" ]; then
    echo "Data file not found: $data_file" >&2
    exit 1
  fi
  curl_args+=(--data-binary "@$data_file")
elif [ -n "$data" ]; then
  curl_args+=(--data "$data")
fi

for a in ${extra_curl_args[@]+"${extra_curl_args[@]}"}; do
  curl_args+=("$a")
done

curl_args+=("$url")

exec curl "${curl_args[@]}"
