# Scaling & high availability

The application is split into stateless roles plus stateful backing services.
All persistent state lives in **PostgreSQL** and the **media** volume — scale by
running more of the stateless roles and making those two backends highly
available.

## Roles (stateless — scale horizontally)

| Role  | Command            | Notes |
|-------|--------------------|-------|
| web   | `entrypoint web`   | Gunicorn; run N replicas behind a load balancer |
| smtp  | `entrypoint smtp`  | inbound :25 + submission :587; run N behind a TCP LB |
| imap  | `entrypoint imap`  | :143/:993; run N behind a TCP LB |
| pop3  | `entrypoint pop3`  | :110 |
| queue | `entrypoint queue` | outbound worker; **safe to run many** (see below) |

### Concurrency-safe queue

`runqueue` claims work with `SELECT … FOR UPDATE SKIP LOCKED` inside a
transaction and leases each row before sending, so you can run as many `queue`
workers as you like without double-delivering. (On SQLite this degrades to a
single-worker path automatically.)

### Shared cache + sessions

Set `REDIS_URL=redis://redis:6379/0` so every `web` replica shares the Django
cache and sessions (otherwise sessions are per-process and users get logged out
when the LB moves them). Add a `redis` service to compose or point at a managed
Redis.

## Stateful backends (make these HA yourself)

- **PostgreSQL** — use streaming replication / a managed HA Postgres
  (Patroni, RDS, Cloud SQL, …). Point `POSTGRES_HOST` at the primary/proxy.
- **Media** (`/app/media`: stored `.eml`, attachments, queue bodies) — must be
  shared across replicas. Use a network filesystem (NFS/EFS) or switch
  `STORAGES["default"]` to an object-store backend (e.g. S3 via
  `django-storages`).

## Example: scale with compose

```bash
docker compose up -d --scale web=3 --scale smtp=2 --scale queue=3
```
(Then put a real load balancer / DNS round-robin in front of the published
ports, and move Postgres + media onto HA storage.)

## What's NOT included

No turnkey clustering, leader election, or cross-region failover. These are
deployment concerns; the app is built to run as many stateless replicas as you
provision against HA Postgres + shared media.
