# Django mail server

A self-hosted mail server written **from scratch in Django/Python** — no
Postfix, no Dovecot. The SMTP service, message storage, DKIM signing, outbound
delivery and a webmail UI are all implemented as a Django project.

It's deliberately small and readable: a great way to *understand* how mail
actually works, and a usable mail server for a personal domain.

## Architecture

```
                 ┌──────────────────── Django project ─────────────────────┐
   :25  inbound ─┤ aiosmtpd ─▶ greylist ─▶ spam(SPF/DKIM/Bayes/DNSBL/AV) ─▶   │
                 │              ▶ filters ─▶ storage ─▶ PostgreSQL + media     │
   :587 submit ──┤ aiosmtpd + AUTH ─▶ delivery ─┬─ local  ─▶ storage          │
                 │   DKIM sign ───────────────  └─ remote ─▶ OutboundMessage q │
   queue worker ─┤ runqueue ─▶ MX lookup ─▶ SMTP relay :25 (retry w/ backoff) │
   :143/:993 ────┤ IMAP4rev1 server ─┐                                         │
   :110     ─────┤ POP3 server ──────┴─▶ storage (folders, flags, UIDs)        │
   :8000 web ────┤ Gunicorn ─▶ webmail (search/labels/threads/contacts) +     │
                 │             REST API (/api) + Django admin                  │
   (clamav) ─────┤ ClamAV daemon ◀── INSTREAM virus scan                       │
                 └──────────────────────────────────────────────────────────┘
```

| Piece | Where | What it does |
|-------|-------|--------------|
| **SMTP server** | `mail/smtp/handler.py` + `runsmtp` command | `aiosmtpd`-based; accepts inbound mail for local domains, and authenticated submission |
| **Storage** | `mail/storage.py` | recipient resolution (mailboxes, aliases, catch-all), auth, persisting messages |
| **Delivery** | `mail/delivery.py` | DKIM signing, MX lookup (`dnspython`), SMTP relay (`smtplib`) |
| **DKIM** | `mail/dkimtools.py` | RSA keygen (`cryptography`) + signing (`dkimpy`) |
| **Parsing** | `mail/parsing.py` | RFC 5322 → stored headers/body/attachments |
| **Webmail + admin** | `mail/views.py`, `mail/templates/`, `mail/admin.py` | read/compose/folders + full Django admin |
| **Data model** | `mail/models.py` | `Domain`, `Mailbox` (the auth user), `Alias`, `Message`, `Attachment` |

PostgreSQL stores everything; raw `.eml` files and attachments live on the
`media` volume.

## Features

**Protocols**
- Inbound MTA on port 25 (accepts mail only for known local recipients)
- Authenticated submission on port 587 (SMTP AUTH, optional STARTTLS)
- **IMAP4rev1** server (143 / 993) — works with Thunderbird, Apple Mail, mobile
- **POP3** server (110)
- **Persistent outbound queue** with exponential-backoff retries (`runqueue`)
- Per-domain **DKIM** signing with a key generator that prints the DNS record
- Virtual domains, mailboxes, aliases and `@domain` catch-alls

**Anti-spam / anti-virus (inbound)**
- Spam **scoring**: content heuristics + **DKIM verification** + built-in **SPF**
  evaluator + **Bayesian classifier** (learns from spam/not-spam) + **DNSBL**
  lookups; per-mailbox threshold routes spam to Junk
- **Greylisting** of unseen senders
- **ClamAV** virus scanning (optional, bundled service)

**Mailbox intelligence**
- **Server-side filter rules** (from/to/subject/body, contains/equals/regex →
  move, label, mark read, flag, spam, trash)
- **Labels** with colors, **conversation threading**, **full-text search**
- **Vacation auto-responder** (loop-safe) and per-mailbox **signatures**
- **Contacts / address book**

**Interfaces**
- Webmail: login, folders, conversations, read, compose, reply, attachments,
  flag, labels, search, contacts, settings, view source
- **REST API** (token auth) for folders, messages, sending and contacts
- Full Django admin for everything (domains, mailboxes, aliases, filters,
  labels, queue, tokens, spam corpus, greylist)
- Per-mailbox quota field; CLI + admin account management

## Quick start

```bash
cp .env.example .env          # set DJANGO_SECRET_KEY, MAIL_HOSTNAME, POSTGRES_PASSWORD, hosts
docker compose up -d --build

# create an admin (also a real mailbox you can log into webmail with)
docker compose run --rm web python manage.py createsuperuser

# add another mailbox, and a DKIM key for the domain
docker compose run --rm web python manage.py createmailbox you@example.com
docker compose run --rm web python manage.py gendkim example.com
```

- Webmail + admin: `http://your-host:8000/` (admin at `/admin/`)
- Then publish your DNS records (see [docs/DNS.md](docs/DNS.md)) and point SMTP
  clients at port 587.

Full walkthrough: [docs/SETUP.md](docs/SETUP.md).

## Management commands

```bash
docker compose run --rm web python manage.py createsuperuser            # admin + mailbox
docker compose run --rm web python manage.py createmailbox a@b.com       # mailbox (+ --quota-mb)
docker compose run --rm web python manage.py gendkim example.com         # DKIM key + DNS record
docker compose run --rm web python manage.py runsmtp                     # smtp service
docker compose run --rm web python manage.py runqueue                    # queue service
docker compose run --rm web python manage.py runimap                     # imap service
docker compose run --rm web python manage.py runpop3                     # pop3 service
docker compose run --rm web python manage.py apitoken you@example.com    # mint a REST API token
```

### REST API

```bash
TOKEN=$(docker compose run --rm web python manage.py apitoken you@example.com | grep -oE '\S{40,}')
curl -H "Authorization: Bearer $TOKEN" http://your-host:8000/api/messages/?folder=INBOX
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"to":["x@y.com"],"subject":"hi","body":"hello"}' \
     http://your-host:8000/api/send/
```
Endpoints: `/api/folders/`, `/api/messages/`, `/api/messages/<id>/`,
`/api/messages/<id>/action/`, `/api/send/`, `/api/contacts/`.

Domains, mailboxes and aliases are also fully manageable in the Django admin.

## ⚠️ Scope & honest caveats

This is now a fairly complete mail platform in readable Python: SMTP in/out,
IMAP, POP3, a retrying queue, multi-signal spam filtering (heuristics + SPF +
DKIM + Bayesian + DNSBL + greylisting), optional ClamAV, filters, labels,
threading, search, contacts, a REST API and admin. Still, be clear-eyed about
what it is **not** versus mature suites like mailcow or Gmail:

- **Spam/AV** is solid but lighter than a tuned Rspamd+ClamAV deployment (no
  per-network reputation, OCR, or large rule corpora). The Bayesian classifier
  needs training before it pulls weight.
- **No calendar / CalDAV / CardDAV sync** (there's a contacts store + API, but
  not a sync protocol), **no OAuth2 / XOAUTH2**, and **no mobile/web push**.
  These are large separate products; they're honestly out of scope here.
- **No built-in HA/clustering.** The web/SMTP/IMAP/POP3 roles are stateless and
  scale horizontally, but you'd run Postgres replication + shared media storage
  yourself.
- The IMAP server implements a practical subset (enough for mainstream clients
  to sync), not every RFC extension (no CONDSTORE/QRESYNC/IDLE-push, etc.).
- **Deliverability still depends on DNS + IP reputation**: correct **PTR**,
  **SPF**, **DKIM**, **DMARC**, and a provider that allows **outbound port 25**.
  See [docs/DNS.md](docs/DNS.md).

For a hardened, every-extension suite, use a dedicated stack. For understanding
and owning the whole thing in Python, this goes a long way.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# point at a local postgres (or sqlite) via env vars, then:
python manage.py migrate
python manage.py runserver        # webmail/admin
python manage.py runsmtp          # SMTP listeners (use high ports if non-root)
```

## Layout

```
config/            Django project (settings, urls, wsgi/asgi)
mail/              the mail server app
  models.py        Domain / Mailbox / Alias / Label / Filter / Message /
                   Attachment / OutboundMessage
  parsing.py       message parsing
  storage.py       routing, auth, threading, persistence
  spam.py          scoring (heuristics + DKIM verify + SPF + Bayes + DNSBL)
  bayes.py         Bayesian classifier   rules.py   filter engine
  greylist.py      greylisting           dnsbl.py   DNS blocklists
  antivirus.py     ClamAV (clamd) client
  delivery.py      DKIM signing, outbound queue, MX lookup, SMTP relay
  dkimtools.py     DKIM keygen + signing
  smtp/handler.py  aiosmtpd handler + authenticator
  imap/            IMAP4rev1 server (backend.py + server.py)
  pop3/            POP3 server
  api.py           token-authenticated REST API
  management/commands/   runsmtp, runqueue, runimap, runpop3,
                         gendkim, createmailbox, apitoken
  views.py, urls.py, forms.py, admin.py, templates/
docs/              SETUP and DNS guides
Dockerfile, docker-compose.yml, entrypoint.sh, requirements.txt
```

## License

MIT — see [LICENSE](LICENSE).
