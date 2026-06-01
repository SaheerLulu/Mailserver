# TLS certificates

This directory is mounted read-only into the postfix and dovecot containers at
`/etc/ssl/mail`. Two files are expected:

- `fullchain.pem` — server certificate + intermediate chain
- `privkey.pem`   — matching private key

Generate them with `scripts/setup-tls.sh` (or `make tls-self-signed` /
`make tls-letsencrypt`). The `.pem`/`.key` files are git-ignored — never commit
private keys.
