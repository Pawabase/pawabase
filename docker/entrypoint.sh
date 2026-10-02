#!/bin/sh
# pawabase-service <service>: run one Pawabase service in this container.
# (Not named `pawabase`: the Python kit installs a `pawabase` command, the deploy CLI, on the same PATH.)
set -e

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

migrate() {
  if [ "${PAWABASE_MIGRATE:-true}" = "true" ]; then
    (cd "/app/$1" && python -m database.migrate)
  fi
}

case "$1" in
  api)       migrate api; serve api 8001 ;;
  worker)    run api -m app.worker ;;
  scheduler) run api -m app.scheduler ;;
  akountz)   migrate akountz; serve akountz 8002 ;;
  angula)    serve angula 8003 ;;
  gateway)   serve gateway 8080 ;;
  studio)    serve studio 8090 ;;
  migrate)   migrate api; migrate akountz ;;
  *)
    echo "usage: pawabase-service api|worker|scheduler|akountz|angula|gateway|studio|migrate" >&2
    exit 64 ;;
esac
