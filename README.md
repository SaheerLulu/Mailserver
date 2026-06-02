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

**Gmail-style features**
- **Search operators**: `from:` `to:` `subject:` `label:` `in:` `category:`
  `is:unread|read|starred|important|muted` `has:attachment` `before:`/`after:`
- **Inbox categories/tabs**: Primary / Social / Promotions / Updates / Forums
  (auto-classified on arrival)
- **Drafts** (save / continue / send), **reply-all** & **forward**
- **Scheduled send**, **undo send** (hold window), and **snooze** (with a worker
  that returns mail to the inbox when due)
- **Bulk actions** (select many → read/archive/trash/spam/important),
  **mark all as read**
- **Star**, **mark important**, **mute conversation**
- **HTML compose**, **recipient autocomplete**, **keyboard shortcuts**
  (`c` compose, `/` search, `g i` inbox, `g t` sent)

**Sync & calendaring**
- **CardDAV** contacts sync and **CalDAV** calendar sync (Apple/Thunderbird/DAVx⁵)
- Built-in **calendar** in the webmail (events + ICS)

**Auth & interfaces**
- **OAuth2** token endpoint + **XOAUTH2** SASL for IMAP **and** SMTP
- **REST API** (token auth) for folders, messages, sending and contacts
- **Web Push** notifications (VAPID) + per-mailbox **webhooks** on new mail
- Webmail: folders, conversations, compose/reply, attachments, labels, search,
  contacts, calendar, settings
- Full Django admin for everything (domains, mailboxes, aliases, filters,
  labels, queue, tokens, calendars/events, push subs, spam corpus, greylist)

**Scaling / HA**
- Stateless web/SMTP/IMAP/POP3 roles; **concurrency-safe queue** (SELECT … FOR
  UPDATE SKIP LOCKED) so multiple queue workers run in parallel
- Optional **Redis** for shared cache + sessions across web replicas
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
docker compose run --rm web python manage.py vapidkeys                   # Web Push (VAPID) keys
```

### Sync (CalDAV / CardDAV)

Point a client (Apple Contacts/Calendar, Thunderbird, DAVx⁵) at the server with
the mailbox email + password. Discovery and collection URLs:

```
CardDAV : https://mail.example.com/dav/<email>/addressbook/
CalDAV  : https://mail.example.com/dav/<email>/calendars/default/
(autodiscovery: /.well-known/carddav, /.well-known/caldav)
```

### OAuth2 / XOAUTH2

```bash
# Get a token (password grant) and use it as a bearer or via XOAUTH2:
curl -d grant_type=password -d username=you@example.com -d password=… \
     http://your-host:8000/oauth/token
```
The returned token works as `Authorization: Bearer …` on the REST API and as
the XOAUTH2 credential for IMAP and SMTP clients.

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

This is now a broad mail platform in readable Python: SMTP in/out, IMAP, POP3,
a retrying/concurrency-safe queue, multi-signal spam filtering (heuristics +
SPF + DKIM + Bayesian + DNSBL + greylisting), optional ClamAV, filters, labels,
threading, search, contacts, calendar, CalDAV/CardDAV sync, OAuth2/XOAUTH2,
Web Push + webhooks, a REST API and admin. Honest remaining limits versus a
mature suite like mailcow or Gmail:

- **Spam/AV** is solid but lighter than a tuned Rspamd+ClamAV deployment (no
  per-network reputation, OCR or large rule corpora); Bayes needs training.
- **DAV is a practical subset** — PROPFIND/REPORT/GET/PUT/DELETE and ctag/etag,
  enough for clients to sync, but no `sync-collection` token, scheduling or
  free/busy. **IMAP** likewise omits CONDSTORE/QRESYNC/IDLE-push.
- **OAuth2** implements the password grant + bearer/XOAUTH2; there's no
  authorization-code flow with a consent screen or client registry.
- **Push** is browser/PWA **Web Push** (VAPID). Native **APNs/FCM** push needs
  Apple/Google developer accounts and isn't included.
- **HA** primitives are here (stateless roles, FOR-UPDATE-SKIP-LOCKED queue,
  optional Redis cache/sessions) but you still run **Postgres replication** and
  **shared media storage** yourself; no turnkey clustering.
- **Deliverability still depends on DNS + IP reputation**: correct **PTR**,
  **SPF**, **DKIM**, **DMARC**, and a provider that allows **outbound port 25**.
  See [docs/DNS.md](docs/DNS.md).
- **Not Gmail-the-product**: the *mail* feature set is broad (search operators,
  categories, drafts, scheduled/undo send, snooze, labels, etc.), but there's no
  Drive/Meet/Chat/Spaces, no ML **Smart Compose/Smart Reply**, no priority-inbox
  learning, and the webmail is a clean server-rendered UI rather than Gmail's SPA.

For a hardened, every-extension suite, use a dedicated stack. For understanding
and owning the whole thing in Python, this goes a very long way.

## Tests & CI

A full test suite lives in `mail/tests/` (delivery, spam/Bayes/greylist/DNSBL,
filters, threading, webmail, REST API, OAuth2, CalDAV/CardDAV, notifications,
and **live IMAP/POP3/SMTP-XOAUTH2** servers driven by real `imaplib`/`poplib`/
`smtplib` clients).

```bash
python manage.py test            # against your configured database
```

The protocol tests need a database whose commits are visible across threads, so
they **auto-skip on SQLite** and run on **PostgreSQL**.

CI (`.github/workflows/ci.yml`) runs two jobs on every push:
- **test** — migrations-in-sync + `manage.py check` + the whole suite (incl. the
  live protocol tests) against a Postgres service.
- **integration** — boots the real `docker compose` stack and runs
  `scripts/integration_test.sh`, which creates a mailbox, sends through SMTP
  submission, and reads it back over IMAP and POP3.

You can run the integration script yourself against a running stack:

```bash
docker compose up -d --build postgres web smtp imap pop3 queue
./scripts/integration_test.sh
```

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
  imap/            IMAP4rev1 server (backend.py + server.py; XOAUTH2)
  pop3/            POP3 server
  api.py           token-authenticated REST API
  dav.py + ics.py  CalDAV/CardDAV server + vCard/iCalendar (de)serialization
  oauth.py         OAuth2 token endpoint + XOAUTH2 validation
  notify.py        new-mail webhooks + Web Push (VAPID)
  management/commands/   runsmtp, runqueue, runimap, runpop3,
                         gendkim, createmailbox, apitoken, vapidkeys
  views.py, urls.py, forms.py, admin.py, templates/
docs/              SETUP and DNS guides
Dockerfile, docker-compose.yml, entrypoint.sh, requirements.txt
```

## License

MIT — see [LICENSE](LICENSE).
