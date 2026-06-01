#!/usr/bin/env bash
# Generate a DKIM keypair for a domain and print the DNS record to publish.
# Usage: scripts/gen-dkim.sh <domain> [selector]   (selector defaults to $DKIM_SELECTOR)
source "$(dirname "$0")/_common.sh"

domain="${1:-}"
selector="${2:-${DKIM_SELECTOR:-mail}}"
if [[ -z "$domain" ]]; then
    echo "usage: $0 <domain> [selector]" >&2
    exit 1
fi

mkdir -p dkim
keyfile="/var/lib/rspamd/dkim/${domain}.${selector}.key"
record_host="dkim/${domain}.${selector}.txt"

if [[ -f "dkim/${domain}.${selector}.key" ]]; then
    echo "error: dkim/${domain}.${selector}.key already exists — refusing to overwrite." >&2
    exit 1
fi

# Generate the key inside the rspamd container (writes the private key to the
# shared volume and prints the public-key TXT record to stdout).
"${DC[@]}" exec -T -u root rspamd \
    rspamadm dkim_keygen -s "$selector" -b 2048 -d "$domain" -k "$keyfile" \
    > "$record_host"

# Lock the private key down to the rspamd runtime user.
"${DC[@]}" exec -T -u root rspamd chown _rspamd:_rspamd "$keyfile"
"${DC[@]}" exec -T -u root rspamd chmod 600 "$keyfile"

# Map this domain to its selector (idempotent).
touch dkim/selectors.map
if ! grep -qx "${domain} ${selector}" dkim/selectors.map; then
    echo "${domain} ${selector}" >> dkim/selectors.map
fi

echo "✓ DKIM key generated for ${domain} (selector: ${selector})"
echo "  private key : dkim/${domain}.${selector}.key  (kept out of git)"
echo "  DNS record  : ${record_host}"
echo
echo "Publish the TXT record below, then run 'make reload':"
echo "----------------------------------------------------------------------"
cat "$record_host"
echo "----------------------------------------------------------------------"
