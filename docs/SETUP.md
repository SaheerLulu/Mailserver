# Setup guide

End-to-end walkthrough for bringing the server online. Assumes a fresh
Linux VPS with a **static public IP** and Docker + the Compose plugin
installed.

> Before anything else: confirm your provider lets you send on port 25. Many
> cloud providers (AWS, GCP, Azure, DigitalOcean, Oracle) block outbound 25 by
> default and require a support request to open it. Without it you can receive
> but not send.

## 0. Prerequisites

- A domain you control the DNS for.
- A host whose hostname will be `mail.<your-domain>` with a matching **PTR**
  record (see [DNS.md](DNS.md)).
- Docker Engine 24+ with the Compose v2 plugin (`docker compose version`).
- Ports 25, 465, 587, 143, 993 (and 4190 for ManageSieve) reachable.

## 1. Clone and configure

```bash
git clone <this-repo> mailserver && cd mailserver
cp .env.example .env
$EDITOR .env          # set MAIL_DOMAIN, MAIL_HOSTNAME, a strong POSTGRES_PASSWORD
```

## 2. TLS certificate

For a real deployment (point `mail.example.com`'s A record at this host first,
and make sure port 80 is free):

```bash
make tls-letsencrypt
```

Just trying it out locally? Use a throwaway self-signed cert instead:

```bash
make tls-self-signed
```

## 3. Build and start

```bash
make build
make up
make ps          # all services should become healthy/running
make logs        # watch startup; Ctrl-C to detach
```

## 4. Create your domain, mailbox and DKIM key

```bash
make add-domain DOMAIN=example.com
make add-user   EMAIL=you@example.com            # prompts for a password
make dkim       DOMAIN=example.com               # prints the DKIM DNS record
make reload
```

Optional alias / catch-all:

```bash
make add-alias SRC=info@example.com DST=you@example.com
make add-alias SRC=@example.com     DST=you@example.com   # catch-all
```

## 5. Publish DNS records

Work through [DNS.md](DNS.md): A/AAAA, PTR, MX, SPF, the DKIM record from
step 4, and DMARC. Give them time to propagate.

## 6. Connect a mail client

| Setting        | Value                                  |
|----------------|----------------------------------------|
| IMAP server    | `mail.example.com`, port **993**, SSL/TLS |
| SMTP server    | `mail.example.com`, port **587**, STARTTLS (or **465**, SSL/TLS) |
| Username       | full email address, e.g. `you@example.com` |
| Password       | the one you set in step 4              |

## 7. Verify deliverability

Send a message from your new mailbox to the address shown on
<https://www.mail-tester.com> and aim for 10/10. Then send to a Gmail account
and check the headers show `spf=pass`, `dkim=pass`, `dmarc=pass`.

## Day-2 operations

- **List everything:** `make list`
- **Reload config:** `make reload`
- **Delete a mailbox:** `make del-user EMAIL=you@example.com`
  (removes the account; its maildir on the `maildata` volume is left in place —
  delete it manually with
  `docker compose exec dovecot rm -rf /var/mail/vhosts/example.com/you` if you
  really want the data gone).
- **Rspamd web UI:** it's bound to `127.0.0.1:11334`. Reach it over an SSH
  tunnel: `ssh -L 11334:127.0.0.1:11334 user@host`, then open
  `http://localhost:11334`. Set a controller password first
  (`rspamd/local.d/worker-controller.inc`).
- **Backups:** the important state is the `postgres-data` and `maildata`
  volumes plus the `dkim/` directory and `certs/`. Snapshot/`pg_dump` regularly.
- **Cert renewal:** see the note printed by `make tls-letsencrypt`; renew and
  `make reload` (automate via cron).
