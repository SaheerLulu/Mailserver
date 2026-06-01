FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postfix \
        postfix-pgsql \
        ca-certificates \
        gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Static configuration. Dynamic values (hostname, DB credentials) are applied
# at container start by entrypoint.sh.
COPY conf/main.cf   /etc/postfix/main.cf
COPY conf/master.cf /etc/postfix/master.cf
COPY conf/pgsql/    /etc/postfix/pgsql/
COPY entrypoint.sh  /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 25 465 587

ENTRYPOINT ["/entrypoint.sh"]
