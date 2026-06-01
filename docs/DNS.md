# DNS records

Mail delivery — and especially *not landing in spam* — depends almost entirely
on correct DNS. Set these up for **every** domain you send/receive mail for.
Examples use `example.com` with the server hostname `mail.example.com` at
public IP `203.0.113.10` (replace with your own).

## 1. Host A / AAAA — where the server lives

| Type | Name              | Value          |
|------|-------------------|----------------|
| A    | `mail.example.com`| `203.0.113.10` |
| AAAA | `mail.example.com`| `2001:db8::10` (if you have IPv6) |

## 2. PTR (reverse DNS) — set at your hosting/VPS provider

`203.0.113.10  →  mail.example.com`

The PTR record is configured where your IP is allocated (VPS control panel),
**not** in your domain's zone. Many receivers reject mail from IPs whose PTR
doesn't resolve back to the sending hostname. This is the single most common
reason self-hosted mail gets blocked.

## 3. MX — where mail for the domain is delivered

| Type | Name          | Priority | Value              |
|------|---------------|----------|--------------------|
| MX   | `example.com` | `10`     | `mail.example.com` |

## 4. SPF — who is allowed to send for the domain

| Type | Name          | Value                              |
|------|---------------|------------------------------------|
| TXT  | `example.com` | `v=spf1 mx -all`                   |

`mx` authorises your MX host; `-all` says "reject anything else". Use `~all`
(softfail) while testing if you're unsure.

## 5. DKIM — cryptographic signature

Generate the key and get the exact record to publish:

```bash
make dkim DOMAIN=example.com
```

That prints a TXT record for `mail._domainkey.example.com` (the selector is
`mail` by default — set `DKIM_SELECTOR` in `.env` to change it). Publish it,
then `make reload`.

| Type | Name                          | Value                          |
|------|-------------------------------|--------------------------------|
| TXT  | `mail._domainkey.example.com` | `v=DKIM1; k=rsa; p=MIIBIj...`  |

## 6. DMARC — policy + reporting

| Type | Name                  | Value                                                        |
|------|-----------------------|--------------------------------------------------------------|
| TXT  | `_dmarc.example.com`  | `v=DMARC1; p=quarantine; rua=mailto:postmaster@example.com; adkim=s; aspf=s` |

Start with `p=none` to monitor (you'll get reports without affecting
delivery), then tighten to `quarantine` and finally `reject` once SPF+DKIM are
verified passing.

## 7. (Recommended) MTA-STS & TLS reporting — optional but nice

- `_mta-sts.example.com` TXT + an HTTPS-served policy file enforces TLS.
- `_smtp._tls.example.com` TXT enables TLS-RPT reports.

## Verifying

```bash
dig +short MX example.com
dig +short TXT example.com                       # SPF
dig +short TXT mail._domainkey.example.com        # DKIM
dig +short TXT _dmarc.example.com                 # DMARC
dig +short -x 203.0.113.10                        # PTR
```

External checkers worth using: **mail-tester.com** (send it a mail, get a
0–10 score with per-record diagnostics) and **MXToolbox**.
