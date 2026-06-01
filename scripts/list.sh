#!/usr/bin/env bash
# Show registered domains, mailboxes and aliases.
# Usage: scripts/list.sh
source "$(dirname "$0")/_common.sh"

echo "== Domains =="
psql_exec -c "SELECT name FROM virtual_domains ORDER BY name;"

echo "== Mailboxes =="
psql_exec -c "SELECT email,
                     CASE WHEN quota_bytes = 0 THEN 'unlimited'
                          ELSE (quota_bytes / 1048576)::text || ' MB' END AS quota,
                     enabled
              FROM virtual_users ORDER BY email;"

echo "== Aliases =="
psql_exec -c "SELECT source, destination FROM virtual_aliases ORDER BY source;"
