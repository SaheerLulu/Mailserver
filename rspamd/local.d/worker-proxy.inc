# Milter / proxy worker — Postfix talks to this on port 11332.
# self_scan mode means this worker both speaks the milter protocol AND scans
# the message itself (no separate normal worker needed for small setups).
milter = yes;
timeout = 120s;
upstream "local" {
  default = yes;
  self_scan = yes;
}
count = 4;
max_retries = 5;
discard_on_reject = false;
quarantine_on_reject = false;
