#!/bin/sh
# ============================================================================
#  Postfix container entrypoint
#  - renders PostgreSQL lookup maps from templates (injects DB credentials)
#  - applies dynamic identity settings (hostname / domain)
#  - runs Postfix in the foreground so Docker can supervise it
# ============================================================================
set -eu

: "${MAIL_HOSTNAME:?MAIL_HOSTNAME is required}"
: "${MAIL_DOMAIN:?MAIL_DOMAIN is required}"
: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

export POSTGRES_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD

echo "[postfix] rendering PostgreSQL lookup maps"
for tmpl in /etc/postfix/pgsql/*.cf.tmpl; do
    [ -e "$tmpl" ] || continue
    out="${tmpl%.tmpl}"
    envsubst '${POSTGRES_HOST} ${POSTGRES_DB} ${POSTGRES_USER} ${POSTGRES_PASSWORD}' \
        < "$tmpl" > "$out"
    chmod 640 "$out"
done

echo "[postfix] applying identity (hostname=${MAIL_HOSTNAME} domain=${MAIL_DOMAIN})"
postconf -e "myhostname=${MAIL_HOSTNAME}"
postconf -e "mydomain=${MAIL_DOMAIN}"
postconf -e "myorigin=${MAIL_DOMAIN}"

# Ensure the (otherwise empty) aliases db that Postfix expects exists.
touch /etc/aliases
newaliases 2>/dev/null || true

# Refresh spool ownership/structure in case the volume is fresh.
postfix set-permissions 2>/dev/null || true

echo "[postfix] starting"
exec /usr/sbin/postfix -c /etc/postfix start-fg
