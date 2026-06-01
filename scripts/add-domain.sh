#!/usr/bin/env bash
# Register a virtual domain this server accepts mail for.
# Usage: scripts/add-domain.sh <domain>
source "$(dirname "$0")/_common.sh"

domain="${1:-}"
if [[ -z "$domain" ]]; then
    echo "usage: $0 <domain>" >&2
    exit 1
fi
domain="$(sql_escape "$domain")"

psql_exec -c "INSERT INTO virtual_domains (name) VALUES ('$domain')
              ON CONFLICT (name) DO NOTHING;"
echo "✓ domain '$1' is registered"
