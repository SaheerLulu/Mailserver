# Controller worker — web UI and API on :11334 (published to 127.0.0.1 only).
#
# Set a password before exposing anything! Generate a hash with:
#     docker compose exec rspamd rspamadm pw
# then uncomment and paste it below.
#
# password = "$2$....";
# enable_password = "$2$....";

bind_socket = "*:11334";
secure_ip = "127.0.0.1";
