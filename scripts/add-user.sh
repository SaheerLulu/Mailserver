#!/usr/bin/env bash
# Create (or update) a mailbox account.
# Usage: scripts/add-user.sh <email> [password] [quota_mb]
#   - if password is omitted you'll be prompted (input hidden)
#   - quota_mb defaults to 0 (unlimited)
source "$(dirname "$0")/_common.sh"

email="${1:-}"
password="${2:-}"
quota_mb="${3:-0}"

if [[ -z "$email" || "$email" != *@* ]]; then
    echo "usage: $0 <email> [password] [quota_mb]" >&2
    exit 1
fi

domain="${email#*@}"

if [[ -z "$password" ]]; then
    read -r -s -p "Password for ${email}: " password; echo
    read -r -s -p "Confirm password: " confirm; echo
    [[ "$password" == "$confirm" ]] || { echo "error: passwords do not match" >&2; exit 1; }
fi

quota_bytes=$(( quota_mb * 1024 * 1024 ))
hash="$(hash_password "$password")"

e_email="$(sql_escape "$email")"
e_domain="$(sql_escape "$domain")"
e_hash="$(sql_escape "$hash")"

# Ensure the domain exists, then upsert the user.
psql_exec <<SQL
INSERT INTO virtual_domains (name) VALUES ('$e_domain')
  ON CONFLICT (name) DO NOTHING;

INSERT INTO virtual_users (domain_id, email, password, quota_bytes)
SELECT id, '$e_email', '$e_hash', $quota_bytes
  FROM virtual_domains WHERE name = '$e_domain'
ON CONFLICT (email)
  DO UPDATE SET password = EXCLUDED.password,
                quota_bytes = EXCLUDED.quota_bytes,
                enabled = TRUE;
SQL

echo "✓ mailbox '${email}' ready (quota: ${quota_mb} MB; 0 = unlimited)"
