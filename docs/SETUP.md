# Setup guide

Bringing the Django mail server online on a public Linux host with Docker.

> **Outbound port 25:** many cloud providers block it by default and require a
> support request to open it. Without it you can receive but not send to the
> internet.

## 1. Prerequisites

- A domain whose DNS you control.
- A host with a static public IP, hostname `mail.<domain>`, and a **PTR** record
  that resolves back to it (set at your VPS provider).
- Docker Engine + Compose v2.
- Ports 25, 587 and 8000 reachable (put a TLS-terminating reverse proxy in
  front of 8000 for the webmail in production).

## 2. Configure

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"   # -> DJANGO_SECRET_KEY
$EDITOR .env     # set the secret, MAIL_HOSTNAME, DJANGO_ALLOWED_HOSTS, POSTGRES_PASSWORD
```

## 3. (Optional) STARTTLS for SMTP

Drop a certificate and key into `./certs/` and point `SMTP_TLS_CERT` /
`SMTP_TLS_KEY` at them (container paths, e.g. `/app/certs/fullchain.pem`). With
TLS configured, the submission port advertises STARTTLS and requires it before
AUTH. You can obtain a cert with certbot:

```bash
docker run --rm -p 80:80 -v "$PWD/certs:/etc/letsencrypt" \
  certbot/certbot certonly --standalone -d mail.example.com
# then copy live/<host>/fullchain.pem and privkey.pem into ./certs
```

## 4. Build and start

```bash
docker compose up -d --build
docker compose ps          # postgres + web healthy, smtp running
docker compose logs -f     # watch startup
```

The `web` service runs migrations automatically on start.

## 5. Create accounts and a DKIM key

```bash
# Admin account — this is also a real mailbox you can use in webmail.
docker compose run --rm web python manage.py createsuperuser

# More mailboxes (optionally with a quota in MB):
docker compose run --rm web python manage.py createmailbox you@example.com --quota-mb 2048

# DKIM key — prints the DNS record to publish:
docker compose run --rm web python manage.py gendkim example.com
```

Aliases and catch-alls: add them in the admin (`/admin/mail/alias/`), e.g.
source `info@example.com` → destination `you@example.com`, or a catch-all with
source `@example.com`.

## 6. Publish DNS

Work through [DNS.md](DNS.md): **A/AAAA, PTR, MX, SPF, DKIM** (from step 5),
**DMARC**. Allow time to propagate, then test at
<https://www.mail-tester.com>.

## 7. Use it

- **Webmail + admin:** `http://mail.example.com:8000/` (`/admin/` for admin).
  Behind a reverse proxy, serve it over HTTPS and add the origin to
  `DJANGO_CSRF_TRUSTED_ORIGINS`.
- **Desktop/phone SMTP (sending):** server `mail.example.com`, port **587**,
  STARTTLS (if you configured TLS), username = full email, password = mailbox
  password. (Reading is via webmail — there is no IMAP.)

## Day-2

- **Logs:** `docker compose logs -f smtp` / `... web`
- **Backups:** the `postgres-data` and `media` Docker volumes hold all mail.
  `docker compose exec postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"` and
  archive the `media` volume.
- **Add/disable accounts:** Django admin, or `createmailbox`. Set a mailbox
  `is_active=False` to disable it without deleting mail.
- **Rotate DKIM:** `gendkim example.com --force`, then republish the record.
