#!/bin/sh
# ============================================================================
#  Dovecot container entrypoint
#  - renders the SQL connection config (injects DB credentials)
#  - precompiles the default Sieve script
#  - runs Dovecot in the foreground
# ============================================================================
set -eu

: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

export POSTGRES_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD

echo "[dovecot] rendering SQL configuration"
envsubst '${POSTGRES_HOST} ${POSTGRES_DB} ${POSTGRES_USER} ${POSTGRES_PASSWORD}' \
    < /etc/dovecot/dovecot-sql.conf.ext.tmpl \
    > /etc/dovecot/dovecot-sql.conf.ext
chmod 600 /etc/dovecot/dovecot-sql.conf.ext

# The SQL config holds the DB password; keep it readable only by root/dovecot.
chown root:root /etc/dovecot/dovecot-sql.conf.ext || true

echo "[dovecot] compiling default sieve script"
sievec /etc/dovecot/sieve/default.sieve 2>/dev/null || true

mkdir -p /var/mail/vhosts
chown vmail:vmail /var/mail/vhosts

echo "[dovecot] starting"
exec dovecot -F
