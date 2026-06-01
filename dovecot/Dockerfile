FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        dovecot-core \
        dovecot-imapd \
        dovecot-lmtpd \
        dovecot-pgsql \
        dovecot-sieve \
        dovecot-managesieved \
        gettext-base \
    && rm -rf /var/lib/apt/lists/* \
    # Dedicated unprivileged owner for all mailboxes.
    && groupadd -g 5000 vmail \
    && useradd  -u 5000 -g vmail -d /var/mail/vhosts -s /usr/sbin/nologin vmail \
    && mkdir -p /var/mail/vhosts \
    && chown -R vmail:vmail /var/mail/vhosts

# Replace the stock config tree with ours.
RUN rm -f /etc/dovecot/dovecot.conf /etc/dovecot/conf.d/*
COPY conf/dovecot.conf            /etc/dovecot/dovecot.conf
COPY conf/conf.d/                 /etc/dovecot/conf.d/
COPY conf/dovecot-sql.conf.ext.tmpl /etc/dovecot/dovecot-sql.conf.ext.tmpl
COPY conf/sieve/                  /etc/dovecot/sieve/
COPY entrypoint.sh                /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 143 993 4190 24 12345

ENTRYPOINT ["/entrypoint.sh"]
