-- ============================================================================
--  Mail server database schema
--
--  Loaded automatically by the postgres container on first start
--  (docker-entrypoint-initdb.d). Defines the virtual domains, mailboxes and
--  aliases that Postfix and Dovecot look up at runtime.
-- ============================================================================

-- Domains this server is authoritative for (virtual mailbox domains).
CREATE TABLE IF NOT EXISTS virtual_domains (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE
);

-- Mailbox accounts. `password` holds a crypt-scheme hash understood by
-- Dovecot (BLF-CRYPT / bcrypt by default — see scripts/add-user.sh).
-- `quota_bytes` of 0 means unlimited.
CREATE TABLE IF NOT EXISTS virtual_users (
    id          SERIAL PRIMARY KEY,
    domain_id   INTEGER NOT NULL REFERENCES virtual_domains(id) ON DELETE CASCADE,
    email       VARCHAR(255) NOT NULL UNIQUE,
    password    VARCHAR(255) NOT NULL,
    quota_bytes BIGINT NOT NULL DEFAULT 0,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Address forwarding. A `source` address is delivered to `destination`
-- (which may itself be a local mailbox or a remote address). Catch-all is
-- expressed as "@domain.tld".
CREATE TABLE IF NOT EXISTS virtual_aliases (
    id          SERIAL PRIMARY KEY,
    domain_id   INTEGER NOT NULL REFERENCES virtual_domains(id) ON DELETE CASCADE,
    source      VARCHAR(255) NOT NULL,
    destination VARCHAR(255) NOT NULL,
    UNIQUE (source, destination)
);

CREATE INDEX IF NOT EXISTS idx_virtual_users_email   ON virtual_users (email);
CREATE INDEX IF NOT EXISTS idx_virtual_aliases_source ON virtual_aliases (source);
