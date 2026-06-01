# Troubleshooting

## Inspecting the system

```bash
make logs                              # everything
docker compose logs -f postfix          # one service
docker compose exec postfix postqueue -p   # the mail queue
docker compose exec dovecot doveadm who    # who's connected over IMAP
```

## Common problems

### Mail I send lands in spam / is rejected

Almost always DNS. Work through [DNS.md](DNS.md) and confirm **PTR**, **SPF**,
**DKIM** and **DMARC** all pass — `mail-tester.com` will tell you which one is
failing. A missing/incorrect PTR is the usual culprit.

### Can't send at all (connections to port 25 time out)

Your provider is probably blocking outbound port 25. Test from the host:
`nc -zv gmail-smtp-in.l.google.com 25`. If it hangs, open a support ticket to
unblock it.

### `make add-user` / scripts fail with a DB or connection error

The stack must be running (`make up`) and the postgres container healthy
(`make ps`). The scripts read credentials from `.env` and exec into the
containers, so run them from the repo root.

### "Relay access denied" when sending from a client

You're connecting to port 25 (which only accepts mail *to* local domains) or
not authenticating. Use port **587**/**465** with your full email address as
the username and authenticate.

### IMAP login fails

- Check the password hash exists: `make list`.
- Watch dovecot logs during the attempt: `docker compose logs -f dovecot`.
- `auth failed` usually means a wrong password or `enabled=false` in the DB.

### TLS certificate warnings in the client

Expected with a self-signed cert (`make tls-self-signed`). Switch to
`make tls-letsencrypt` for a trusted certificate.

### DKIM signature missing or failing

- Did you run `make reload` after `make dkim`?
- Is the published TXT record an exact match (no extra quotes/whitespace)?
- Check rspamd saw the key: `docker compose logs rspamd | grep -i dkim`.

### Changes to `.cf` / config don't take effect

`main.cf`, `master.cf` and the dovecot conf are baked into the images at build
time, so after editing them run `make build && make up` (or `docker compose up
-d --build`). Runtime data (users/aliases/DKIM/certs) does **not** need a
rebuild — just `make reload`.

## Resetting

```bash
make down                 # stop, keep data
docker compose down -v     # stop and DELETE all volumes (mail + db!) — careful
```
