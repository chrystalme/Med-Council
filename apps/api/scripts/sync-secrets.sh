#!/usr/bin/env bash
# Push secret values from a local .env file into GCP Secret Manager.
#
# Usage:
#   apps/api/scripts/sync-secrets.sh <env> [env_file] [--project ID] [--dry-run] [--yes]
#
# Examples:
#   apps/api/scripts/sync-secrets.sh dev                              # reads apps/api/.env.dev
#   apps/api/scripts/sync-secrets.sh dev apps/api/.env.dev --dry-run
#   apps/api/scripts/sync-secrets.sh prod apps/api/.env --yes
#
# Secret names come from terraform/variables.tf:secret_names — keep that list
# and the KEYS array below in sync. All envs currently share one set of
# Secret Manager entries (per the "all keys stay the same for now" decision);
# the env arg picks the default .env file and is logged for clarity.

set -euo pipefail

ENV_ARG="${1:-}"
shift || true

if [[ -z "$ENV_ARG" || "$ENV_ARG" == "-h" || "$ENV_ARG" == "--help" ]]; then
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

case "$ENV_ARG" in
  dev|prod|test) ;;
  *) echo "error: env must be dev|prod|test, got '$ENV_ARG'" >&2; exit 2 ;;
esac

DEFAULT_ENV_FILE="apps/api/.env.${ENV_ARG}"
[[ "$ENV_ARG" == "prod" ]] && DEFAULT_ENV_FILE="apps/api/.env"
ENV_FILE=""
PROJECT=""
DRY_RUN=0
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -*) echo "error: unknown flag $1" >&2; exit 2 ;;
    *)
      if [[ -z "$ENV_FILE" ]]; then ENV_FILE="$1"; shift
      else echo "error: unexpected arg $1" >&2; exit 2; fi
      ;;
  esac
done
ENV_FILE="${ENV_FILE:-$DEFAULT_ENV_FILE}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: env file not found: $ENV_FILE" >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "error: gcloud not found on PATH" >&2
  exit 1
fi

# Resolve target project — explicit flag wins, else fall back to the active
# gcloud config so we never silently push to the wrong project.
if [[ -z "$PROJECT" ]]; then
  PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "$PROJECT" ]]; then
  echo "error: no project set. Pass --project ID or run 'gcloud config set project ID'." >&2
  exit 1
fi

# Single source of truth: must match terraform/variables.tf:secret_names.
KEYS=(
  OPENROUTER_API_KEY
  OPENAI_API_KEY
  GROQ_API_KEY
  CLERK_ISSUER
  CLERK_SECRET_KEY
  CLERK_AUTHORIZED_PARTIES
  RESEND_API_KEY
  RESEND_FROM_EMAIL
  ONCALL_DOCTOR_EMAIL
  FEEDBACK_SECRET
  DATABASE_URL
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
)

# Pull a single key out of the env file. Strips matching surrounding single
# or double quotes; ignores comments and blank lines.
parse_env_value() {
  local file="$1" key="$2" line raw
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line//[[:space:]]/}" ]] && continue
    if [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*=(.*)$ ]]; then
      raw="${BASH_REMATCH[1]}"
      # Trim leading whitespace.
      raw="${raw#"${raw%%[![:space:]]*}"}"
      # Strip matched surrounding quotes (single OR double).
      if [[ "$raw" =~ ^\"(.*)\"$ ]]; then raw="${BASH_REMATCH[1]}"; fi
      if [[ "$raw" =~ ^\'(.*)\'$ ]]; then raw="${BASH_REMATCH[1]}"; fi
      printf '%s' "$raw"
      return 0
    fi
  done < "$file"
  return 1
}

echo "Syncing secrets"
echo "  env:      $ENV_ARG"
echo "  file:     $ENV_FILE"
echo "  project:  $PROJECT"
echo "  dry-run:  $([[ $DRY_RUN -eq 1 ]] && echo yes || echo no)"
echo

if [[ $ASSUME_YES -ne 1 && $DRY_RUN -ne 1 ]]; then
  read -r -p "Push to $PROJECT? [y/N] " ans
  case "$ans" in y|Y|yes|YES) ;; *) echo "aborted"; exit 0 ;; esac
fi

written=0
skipped=0
missing=()

for key in "${KEYS[@]}"; do
  if val="$(parse_env_value "$ENV_FILE" "$key")"; then
    if [[ -z "$val" ]]; then
      echo "  - $key: empty in env file, skipping"
      skipped=$((skipped + 1))
      continue
    fi
    len=${#val}
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "  ~ $key: would write ($len chars)"
    else
      printf '%s' "$val" \
        | gcloud secrets versions add "$key" \
            --project="$PROJECT" \
            --data-file=- >/dev/null
      echo "  ✓ $key: wrote ($len chars)"
    fi
    written=$((written + 1))
  else
    echo "  - $key: not present in env file, skipping"
    missing+=("$key")
    skipped=$((skipped + 1))
  fi
  unset val
done

echo
echo "Done. written=$written skipped=$skipped"
if [[ ${#missing[@]} -gt 0 && $DRY_RUN -eq 0 ]]; then
  echo "Missing keys (existing secret versions retained): ${missing[*]}"
fi
