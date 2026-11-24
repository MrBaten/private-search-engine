#!/usr/bin/env bash
# Start the full PrivateSearch stack (custom engine + SearXNG + Nginx).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is not installed or not on PATH." >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "Error: the 'docker compose' plugin (v2) is required." >&2
    exit 1
fi

ENV_FILE="searxng/.env"
EXAMPLE="searxng/.env.example"

if [ ! -f "$ENV_FILE" ]; then
    if [ ! -f "$EXAMPLE" ]; then
        echo "Error: $EXAMPLE not found; cannot create $ENV_FILE." >&2
        exit 1
    fi
    cp "$EXAMPLE" "$ENV_FILE"
    echo "Created $ENV_FILE from $EXAMPLE"
fi

# Generate a random SearXNG secret if missing or still the placeholder.
# The value is never printed.
current="$(grep -E '^SEARXNG_SECRET=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
if [ -z "$current" ] || [ "$current" = "change-me" ]; then
    if command -v openssl >/dev/null 2>&1; then
        secret="$(openssl rand -hex 32)"
    else
        secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    fi
    if grep -qE '^SEARXNG_SECRET=' "$ENV_FILE"; then
        tmp="$(mktemp)"
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in
                SEARXNG_SECRET=*) printf 'SEARXNG_SECRET=%s\n' "$secret" ;;
                *) printf '%s\n' "$line" ;;
            esac
        done < "$ENV_FILE" > "$tmp"
        cat "$tmp" > "$ENV_FILE"
        rm -f "$tmp"
    else
        printf 'SEARXNG_SECRET=%s\n' "$secret" >> "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    unset secret
    echo "Generated a random SEARXNG_SECRET in $ENV_FILE"
fi

docker compose up -d --build

cat <<'MSG'

PrivateSearch is starting.

  PrivateSearch (custom engine): http://localhost/
  SearXNG:                       http://localhost/searxng/

Status:  docker compose ps
Stop:    docker compose down
MSG
