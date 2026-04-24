#!/bin/bash
set -euo pipefail

gcloud config set project med-council-agents

KEYS=(
  OPENROUTER_API_KEY
  GROQ_API_KEY
  OPENAI_API_KEY
  CLERK_ISSUER
  CLERK_SECRET_KEY
  CLERK_AUTHORIZED_PARTIES
  RESEND_API_KEY
  RESEND_FROM_EMAIL
  ONCALL_DOCTOR_EMAIL
)

# Read a password-style value showing '*' per character so the user can
# see how much they've typed/pasted. Backspace erases one char + one star.
# Enter finishes. Nothing ever echoed in cleartext.
read_masked() {
  local prompt="$1"
  local var="" char
  printf '%s' "$prompt" >&2

  # Save terminal settings, switch to raw-ish mode (no echo, read 1 byte).
  local old_stty
  old_stty=$(stty -g)
  stty -echo -icanon min 1 time 0

  while IFS= read -r -n 1 char; do
    # Enter / newline -> done.
    if [[ -z "$char" || "$char" == $'\n' || "$char" == $'\r' ]]; then
      break
    fi
    # Backspace / DEL (0x7f or 0x08).
    if [[ "$char" == $'\177' || "$char" == $'\b' ]]; then
      if [[ -n "$var" ]]; then
        var="${var%?}"
        printf '\b \b' >&2
      fi
      continue
    fi
    var+="$char"
    printf '*' >&2
  done

  stty "$old_stty"
  printf '\n' >&2
  printf '%s' "$var"
}

for key in "${KEYS[@]}"; do
  val="$(read_masked "$key (blank to skip): ")"
  if [[ -z "$val" ]]; then
    echo "  -> skipped $key"
    continue
  fi
  len=${#val}
  printf '%s' "$val" | gcloud secrets versions add "$key" --data-file=-
  echo "  -> wrote $key (${len} chars)"
done
