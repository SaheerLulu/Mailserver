#!/usr/bin/env bash
# Forward one address to another.
# Usage: scripts/add-alias.sh <source> <destination>
#   catch-all example: scripts/add-alias.sh @example.com user@example.com
source "$(dirname "$0")/_common.sh"

src="${1:-}"
dst="${2:-}"
if [[ -z "$src" || -z "$dst" ]]; then
    echo "usage: $0 <source> <destination>" >&2
    exit 1
fi

# Domain is the part after @ (handles both user@dom and bare @dom catch-alls).
domain="${src#*@}"

e_src="$(sql_escape "$src")"
e_dst="$(sql_escape "$dst")"
e_domain="$(sql_escape "$domain")"

psql_exec <<SQL
INSERT INTO virtual_domains (name) VALUES ('$e_domain')
  ON CONFLICT (name) DO NOTHING;

INSERT INTO virtual_aliases (domain_id, source, destination)
SELECT id, '$e_src', '$e_dst'
  FROM virtual_domains WHERE name = '$e_domain'
ON CONFLICT (source, destination) DO NOTHING;
SQL

echo "✓ alias '${src}' → '${dst}' created"
