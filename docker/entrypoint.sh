#!/bin/sh
# pawabase-service <service>: run one Pawabase service in this container.
# (Not named `pawabase`: the Python kit installs a `pawabase` command, the deploy CLI, on the same PATH.)
set -e

# PAWABASE_EXTRA_CA_B64: a certificate authority to trust besides the system's, base64-encoded (for a database or storage server that signs with its
# own certificate). The container runs as an unprivileged user, so rather than installing it the system bundle plus the certificate is written to a
# file that Python's ssl module and OpenSSL read through SSL_CERT_FILE.
if [ -n "${PAWABASE_EXTRA_CA_B64:-}" ]; then
  { cat /etc/ssl/certs/ca-certificates.crt; printf '\n'; printf '%s' "$PAWABASE_EXTRA_CA_B64" | base64 -d; } > /tmp/ca-bundle.pem
  export SSL_CERT_FILE=/tmp/ca-bundle.pem
fi

# PAWABASE_RELOAD=true (set by docker-compose.dev.yml) restarts a service
# whenever its own code or pawabase_core changes on the bind-mounted source.
serve() {
  cd "/app/$1"
  if [ "${PAWABASE_RELOAD:-false}" = "true" ]; then
    set -- "$@" --reload --reload-dir "/app/$1" --reload-dir /app/pawabase_core \
      --reload-include "*.html" --reload-include "*.json"
  fi
  local svc=$1 port=$2
  shift 2
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-$port}" \
    --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" \
    --log-level "${LOG_LEVEL:-info}" "$@"
}

# Long-running non-HTTP processes (worker, scheduler): a plain exec in
# production, restarted by watchfiles in dev.
run() {
  cd "/app/$1"
  shift
  if [ "${PAWABASE_RELOAD:-false}" = "true" ]; then
    exec watchfiles --filter python "python $*" /app/api /app/pawabase_core
  fi
  exec python "$@"
}

# PAWABASE_ENSURE_DATABASE=true: make this service's database first. For hosts that cannot mount the init script docker/postgres/init.sql.
ensure_database() {
  if [ "${PAWABASE_ENSURE_DATABASE:-false}" = "true" ]; then
    python -m pawabase_core.ensure_database
  fi
}

migrate() {
  if [ "${PAWABASE_MIGRATE:-true}" = "true" ]; then
    (cd "/app/$1" && python -m database.migrate)
  fi
}

case "$1" in
  api)       migrate api; serve api 8001 ;;
  worker)    run api -m app.worker ;;
  scheduler) run api -m app.scheduler ;;
  akountz)   ensure_database; migrate akountz; serve akountz 8002 ;;
  angula)    serve angula 8003 ;;
  gateway)   serve gateway 8080 ;;
  studio)    serve studio 8090 ;;
  migrate)   migrate api; migrate akountz ;;
  # Every process in this one container (docker-compose.hosted.yml): both databases first, then the supervisor starts the services.
  all)
    AKOUNTZ_URL="$(python -m pawabase_core.supervisor --akountz-url)"
    ensure_database
    python -m pawabase_core.ensure_database "$AKOUNTZ_URL"
    migrate api
    (export PAWABASE_DATABASE_URL="$AKOUNTZ_URL"; migrate akountz)
    cd /app
    PAWABASE_MIGRATE=false exec python -m pawabase_core.supervisor ;;
  *)
    echo "usage: pawabase-service api|worker|scheduler|akountz|angula|gateway|studio|migrate|all" >&2
    exit 64 ;;
esac
