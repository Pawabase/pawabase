#!/bin/sh
# entrypoint [serve|studio|worker|scheduler|migrate|check|version]: prepare the container, then hand over. `studio` runs the control plane;
# `worker` and `scheduler` run the queue worker and the scheduler as processes of their own; everything else is the server.
set -e

# PAWABASE_EXTRA_CA_B64: a certificate authority to trust besides the system's, base64-encoded (for a database or storage server that signs
# with its own certificate). The container runs unprivileged, so rather than installing it the system bundle plus the certificate is
# written to a file that Python's ssl module and OpenSSL read through SSL_CERT_FILE.
if [ -n "${PAWABASE_EXTRA_CA_B64:-}" ]; then
  { cat /etc/ssl/certs/ca-certificates.crt; printf '\n'; printf '%s' "$PAWABASE_EXTRA_CA_B64" | base64 -d; } > /tmp/ca-bundle.pem
  export SSL_CERT_FILE=/tmp/ca-bundle.pem
fi

command="${1:-serve}"

if [ "$command" = "serve" ]; then
  # PAWABASE_ENSURE_DATABASE=true: create the database named in PAWABASE_DATABASE_URL first (for hosts that cannot run an init script).
  if [ "${PAWABASE_ENSURE_DATABASE:-false}" = "true" ]; then
    python -m pawabase_server.core.ensure_database
  fi
  # Pending migrations are applied before the server takes traffic. PAWABASE_MIGRATE=false when something else owns the schema.
  if [ "${PAWABASE_MIGRATE:-true}" = "true" ]; then
    python -m pawabase_server migrate
  fi
fi

if [ "$command" = "studio" ]; then
  exec python -m studio
fi

exec python -m pawabase_server "$@"
