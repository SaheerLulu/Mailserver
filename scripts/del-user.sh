#!/usr/bin/env bash
# Remove a mailbox account from the database.
# Usage: scripts/del-user.sh <email>
# Note: this does NOT delete mail already on disk; see docs/SETUP.md.
source "$(dirname "$0")/_common.sh"

email="${1:-}"
if [[ -z "$email" ]]; then
    echo "usage: $0 <email>" >&2
    exit 1
fi
e_email="$(sql_escape "$email")"

psql_exec -c "DELETE FROM virtual_users WHERE email = '$e_email';"
echo "✓ mailbox '${email}' removed from the database"
echo "  (its maildir under /var/mail/vhosts is left untouched)"
