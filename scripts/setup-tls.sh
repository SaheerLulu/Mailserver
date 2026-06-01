#!/usr/bin/env bash
# Provision the TLS certificate used by Postfix and Dovecot.
#
# Usage:
#   scripts/setup-tls.sh self-signed     # quick cert for local testing
#   scripts/setup-tls.sh letsencrypt     # obtain a real cert via certbot (port 80)
#
# Both modes produce ./certs/fullchain.pem and ./certs/privkey.pem.
source "$(dirname "$0")/_common.sh"

mode="${1:-}"
: "${MAIL_HOSTNAME:?MAIL_HOSTNAME must be set in .env}"
mkdir -p certs

case "$mode" in
  self-signed)
    echo "Generating a self-signed certificate for ${MAIL_HOSTNAME} (valid 825 days)..."
    docker run --rm -v "$PWD/certs:/certs" alpine/openssl req -x509 -nodes \
        -newkey rsa:4096 -days 825 \
        -keyout /certs/privkey.pem \
        -out /certs/fullchain.pem \
        -subj "/CN=${MAIL_HOSTNAME}" \
        -addext "subjectAltName=DNS:${MAIL_HOSTNAME}"
    echo "✓ self-signed cert written to ./certs (clients will warn — fine for testing)"
    ;;

  letsencrypt)
    echo "Requesting a Let's Encrypt certificate for ${MAIL_HOSTNAME}..."
    echo "Port 80 on this host must be free and reachable from the internet."
    docker run --rm -p 80:80 \
        -v "$PWD/certs/letsencrypt:/etc/letsencrypt" \
        certbot/certbot certonly --standalone --non-interactive --agree-tos \
        -m "admin@${MAIL_DOMAIN}" -d "${MAIL_HOSTNAME}"

    live="certs/letsencrypt/live/${MAIL_HOSTNAME}"
    if [[ -f "$live/fullchain.pem" ]]; then
        cp "$live/fullchain.pem" certs/fullchain.pem
        cp "$live/privkey.pem"  certs/privkey.pem
        echo "✓ Let's Encrypt cert installed into ./certs"
        echo "  Renew with: docker run --rm -v \"\$PWD/certs/letsencrypt:/etc/letsencrypt\" certbot/certbot renew"
        echo "  then re-copy fullchain/privkey and run 'make reload'. Automate this via cron."
    else
        echo "error: certbot did not produce a certificate; see output above." >&2
        exit 1
    fi
    ;;

  *)
    echo "usage: $0 {self-signed|letsencrypt}" >&2
    exit 1
    ;;
esac
