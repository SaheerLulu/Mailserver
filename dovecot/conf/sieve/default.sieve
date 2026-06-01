# ============================================================================
#  Default system Sieve script — runs for every inbound message.
#  Rspamd tags spam with "X-Spam: Yes"; file those into the Junk folder.
# ============================================================================
require ["fileinto", "mailbox"];

if header :is "X-Spam" "Yes" {
    fileinto :create "Junk";
    stop;
}
