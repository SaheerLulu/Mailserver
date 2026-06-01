# Self-hosted mail server

A complete, from-scratch mail server you run yourself with Docker Compose.
It assembles the proven open-source mail stack — **Postfix**, **Dovecot** and
**Rspamd** — into a single `docker compose up`, with virtual domains, mailboxes
and aliases stored in **PostgreSQL**.

No turnkey black box: every config file is in this repo, commented, and yours
to read and change.

## What's inside

| Component  | Role                                                              |
|------------|-------------------------------------------------------------------|
| **Postfix**| MTA — receives mail on :25, authenticated submission on :587/:465 |
| **Dovecot**| IMAP (:993/:143), LMTP delivery, SASL auth, Sieve filtering       |
| **Rspamd** | Spam scoring **and** DKIM signing, via the milter protocol        |
| **PostgreSQL** | Source of truth for domains, mailboxes and aliases            |
| **Redis**  | Backing store for Rspamd (Bayes, rate limits, greylisting)        |

```
                        ┌──────────────────────────────────────────┐
   inbound :25  ───────▶│ Postfix ──milter──▶ Rspamd ──▶ Redis      │
   submit :587/:465 ───▶│   │  ▲                (spam + DKIM)        │
                        │   │  └── SASL auth ──┐                     │
                        │   ▼ LMTP :24         │                     │
                        │ Dovecot ◀── IMAP :993│── PostgreSQL ◀──────┘
                        │   (maildir on disk)  (users / aliases)     │
   IMAP :993 ◀──────────│──────────────────────────────────────────┘
```

## Features

- Virtual domains / mailboxes / aliases (incl. catch-all) in PostgreSQL
- Authenticated submission only over TLS; senders restricted to addresses they own
- Inbound spam filtering with automatic filing into **Junk** via Sieve
- Per-domain **DKIM** signing; helper generates the key and the DNS record
- Per-mailbox quotas
- TLS via Let's Encrypt (or self-signed for testing)
- One-command management through a `Makefile`

## Quick start

```bash
cp .env.example .env          # then edit: MAIL_DOMAIN, MAIL_HOSTNAME, POSTGRES_PASSWORD
make tls-letsencrypt          # or: make tls-self-signed  (for testing)
make build && make up

make add-domain DOMAIN=example.com
make add-user   EMAIL=you@example.com
make dkim       DOMAIN=example.com        # prints the DKIM DNS record
make reload
```

Then publish your DNS records (see below) and point a mail client at
`mail.example.com` (IMAP 993 / SMTP 587). The full walkthrough is in
[docs/SETUP.md](docs/SETUP.md).

## ⚠️ This is the easy part

The software runs in minutes; **deliverability is the real work** and lives
entirely in DNS and your IP's reputation. You must get all of these right:

- **PTR (reverse DNS)** matching `MAIL_HOSTNAME` — set at your VPS provider
- **MX**, **SPF**, **DKIM**, **DMARC** records
- An IP/provider that **allows outbound port 25** (many block it by default)

[docs/DNS.md](docs/DNS.md) has every record with copy-paste examples. Test the
result at [mail-tester.com](https://www.mail-tester.com).

## Managing the server

| Command | Does |
|---------|------|
| `make up` / `make down` | start / stop the stack |
| `make ps` / `make logs` | status / tail logs |
| `make reload` | reload config without downtime |
| `make add-domain DOMAIN=…` | register a domain |
| `make add-user EMAIL=… [QUOTA=mb]` | create/update a mailbox |
| `make del-user EMAIL=…` | remove a mailbox |
| `make add-alias SRC=… DST=…` | forward an address (catch-all: `SRC=@domain`) |
| `make dkim DOMAIN=…` | generate a DKIM key + DNS record |
| `make list` | list domains, mailboxes, aliases |

Run `make help` for the full list.

## Repository layout

```
docker-compose.yml      service orchestration
.env.example            configuration template
Makefile                management commands
db/init/                PostgreSQL schema (auto-loaded on first run)
postfix/                Postfix image, config and PostgreSQL lookup maps
dovecot/                Dovecot image, config and SQL queries
rspamd/                 Rspamd config (spam + DKIM)
scripts/                domain/user/alias/DKIM/TLS helpers
certs/                  TLS cert + key (git-ignored)
dkim/                   DKIM keys + DNS records (private keys git-ignored)
docs/                   SETUP, DNS and TROUBLESHOOTING guides
```

## Documentation

- [docs/SETUP.md](docs/SETUP.md) — full deployment walkthrough
- [docs/DNS.md](docs/DNS.md) — every DNS record explained
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — when things misbehave

## Security notes

- Set a strong `POSTGRES_PASSWORD` and a Rspamd controller password before
  going live.
- The Rspamd UI is bound to `127.0.0.1` only — reach it via SSH tunnel.
- Private keys (`certs/*.pem`, `dkim/*.key`) and `.env` are git-ignored; keep
  them backed up but never commit them.

## License

MIT — see [LICENSE](LICENSE).
