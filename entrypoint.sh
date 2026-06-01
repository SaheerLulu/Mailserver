#!/bin/sh
# Container entrypoint. First argument selects the role:
#   web   — run migrations, then the Gunicorn web/webmail server
#   smtp  — run the SMTP listeners (inbound MTA + submission)
# Anything else is executed verbatim (e.g. `manage.py createsuperuser`).
set -e

case "${1:-web}" in
  web)
    echo "[web] applying database migrations"
    python manage.py migrate --noinput
    echo "[web] starting gunicorn on :8000"
    exec gunicorn config.wsgi:application \
        --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-3}" --timeout 120
    ;;

  smtp)
    echo "[smtp] starting SMTP listeners"
    exec python manage.py runsmtp
    ;;

  *)
    exec "$@"
    ;;
esac
