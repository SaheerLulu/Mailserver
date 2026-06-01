#!/usr/bin/env bash
# Shared helpers for the management scripts. Sourced, not executed directly.
set -euo pipefail

# Resolve the repository root regardless of where the script is called from.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
    echo "error: .env not found. Copy .env.example to .env and edit it first." >&2
    exit 1
fi

# Load configuration (POSTGRES_*, MAIL_*, etc).
set -a
# shellcheck disable=SC1091
source .env
set +a

# Pick whichever compose invocation is available.
if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
else
    echo "error: neither 'docker compose' nor 'docker-compose' is installed." >&2
    exit 1
fi

# Run psql inside the postgres container. Extra args are passed through.
psql_exec() {
    "${DC[@]}" exec -T postgres \
        psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"
}

# Hash a plaintext password using Dovecot (bcrypt / BLF-CRYPT).
hash_password() {
    local plain="$1"
    "${DC[@]}" exec -T dovecot doveadm pw -s BLF-CRYPT -p "$plain" | tr -d '\r\n'
}

# Escape a value for safe interpolation into single-quoted SQL.
sql_escape() { printf "%s" "${1//\'/\'\'}"; }
