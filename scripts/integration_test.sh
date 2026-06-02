#!/usr/bin/env bash
# End-to-end test against a live docker-compose stack: create an account, send a
# message through SMTP submission, then read it back over IMAP and POP3.
#
# Usage:  docker compose up -d --build postgres web smtp imap pop3 queue
#         ./scripts/integration_test.sh
set -euo pipefail

DC="docker compose"
EMAIL="alice@example.com"
PASS="Sup3rSecretPass1"

echo "==> waiting for the web service"
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8000/login/ >/dev/null 2>&1; then break; fi
  sleep 2
  [ "$i" = 60 ] && { echo "web did not come up"; exit 1; }
done

echo "==> creating mailbox $EMAIL"
$DC exec -T web python manage.py createmailbox "$EMAIL" --password "$PASS" --name Alice

echo "==> waiting for SMTP/IMAP/POP3 ports"
for port in 587 143 110; do
  for i in $(seq 1 30); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then exec 3>&- ; break; fi
    sleep 1
    [ "$i" = 30 ] && { echo "port $port not listening"; exit 1; }
  done
done

echo "==> running mail flow (SMTP send -> IMAP + POP3 read)"
EMAIL="$EMAIL" PASS="$PASS" python3 - <<'PY'
import imaplib, poplib, smtplib, os, sys, time
from email.message import EmailMessage

email, pw = os.environ["EMAIL"], os.environ["PASS"]

# --- send via authenticated submission (587) ---
msg = EmailMessage()
msg["From"] = email
msg["To"] = email
msg["Subject"] = "integration-test"
msg.set_content("hello from the integration test")

s = smtplib.SMTP("127.0.0.1", 587, timeout=15)
s.ehlo("ci")
s.login(email, pw)
s.send_message(msg)
s.quit()
print("   sent via SMTP submission")
time.sleep(2)  # delivery is synchronous, but give the worker a beat

# --- read via IMAP (143) ---
M = imaplib.IMAP4("127.0.0.1", 143)
M.login(email, pw)
typ, data = M.select("INBOX")
assert typ == "OK", data
typ, ids = M.search(None, "ALL")
assert ids[0].split(), "no messages in INBOX over IMAP"
typ, fetched = M.fetch(ids[0].split()[-1], "(RFC822)")
assert b"integration-test" in fetched[0][1], "message body not found over IMAP"
M.logout()
print("   read back over IMAP")

# --- read via POP3 (110) ---
P = poplib.POP3("127.0.0.1", 110, timeout=15)
P.user(email); P.pass_(pw)
count, _ = P.stat()
assert count >= 1, "no messages over POP3"
P.quit()
print("   read back over POP3")

print("INTEGRATION TEST PASSED")
PY
