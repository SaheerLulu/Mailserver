# Django mail server

A self-hosted mail server written **from scratch in Django/Python** — no
Postfix, no Dovecot. The SMTP service, message storage, DKIM signing, outbound
delivery and a webmail UI are all implemented as a Django project.

It's deliberately small and readable: a great way to *understand* how mail
actually works, and a usable mail server for a personal domain.

## Architecture

```
                 ┌──────────────────── Django project ─────────────────────┐
   :25  inbound ─┤ aiosmtpd handler ─▶ storage ─▶ PostgreSQL (Message rows) │
                 │     (MailHandler)        │            + media/*.eml       │
   :587 submit ──┤ aiosmtpd + AUTH ─▶ delivery ─┬─ local  ─▶ storage          │
                 │                              └─ remote ─▶ MX lookup +      │
                 │                                            SMTP relay :25  │
                 │ DKIM signing (dkimpy) on outbound ─────────────────────────│
   :8000 web ────┤ Gunicorn ─▶ webmail views + Django admin                  │
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

- Inbound MTA on port 25 (accepts mail only for known local recipients)
- Authenticated submission on port 587 (SMTP AUTH, optional STARTTLS)
- Outbound delivery to the internet via MX lookup + SMTP relay
- Per-domain **DKIM** signing with a key generator that prints the DNS record
- Virtual domains, mailboxes, aliases and `@domain` catch-alls
- Webmail: login, folders (Inbox/Sent/Drafts/Junk/Archive/Trash), read,
  compose, reply, attachments, flag, view source
- Django admin for managing everything
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
docker compose run --rm web python manage.py runsmtp                     # (the smtp service runs this)
```

Domains, mailboxes and aliases are also fully manageable in the Django admin.

## ⚠️ Scope & honest caveats

This implements a working SMTP MTA + webmail in Python. It is excellent for
learning and for a low-volume personal domain, but it is **not** as
battle-hardened as Postfix/Dovecot. Notably:

- **No IMAP/POP3** — mail is read through the webmail UI (or the admin).
- Outbound delivery is best-effort (tries MX hosts in order); there is no
  persistent retry queue, so transient remote failures are logged, not retried.
- No greylisting/RBL/Bayesian spam filtering. Inbound is accepted for valid
  local recipients; add filtering if you need it.
- **Deliverability still depends on DNS + IP reputation**: correct **PTR**,
  **SPF**, **DKIM**, **DMARC**, and a provider that allows **outbound port 25**.
  See [docs/DNS.md](docs/DNS.md).

For a hardened, full-protocol stack, use a dedicated MTA/MDA. For understanding
and owning the whole thing in Python, this is for you.

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
  models.py        Domain / Mailbox / Alias / Message / Attachment
  parsing.py       message parsing
  storage.py       routing, auth, persistence
  delivery.py      DKIM signing, MX lookup, SMTP relay
  dkimtools.py     DKIM keygen + signing
  smtp/handler.py  aiosmtpd handler + authenticator
  management/commands/   runsmtp, gendkim, createmailbox
  views.py, urls.py, forms.py, admin.py, templates/
docs/              SETUP and DNS guides
Dockerfile, docker-compose.yml, entrypoint.sh, requirements.txt
```

## License

MIT — see [LICENSE](LICENSE).
