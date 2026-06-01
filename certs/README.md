# TLS certificates (optional)

Mounted read-only into the `smtp` container at `/app/certs`. To enable STARTTLS
on the SMTP ports, place a certificate + key here and point `SMTP_TLS_CERT` /
`SMTP_TLS_KEY` in `.env` at them (e.g. `/app/certs/fullchain.pem`).

`.pem` / `.key` files are git-ignored — never commit private keys. See
`docs/SETUP.md` for obtaining a certificate with certbot.
