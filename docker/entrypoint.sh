#!/bin/sh
# pawabase <service>: run one Pawabase service in this container.
set -e

serve() {
  cd "/app/services/$1"
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-$2}" \
    --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" \
    --log-level "${LOG_LEVEL:-info}"
}

migrate() {
  if [ "${PAWABASE_MIGRATE:-true}" = "true" ]; then
    (cd "/app/services/$1" && python -m database.migrate)
  fi
}

case "$1" in
  api)       migrate api; serve api 8001 ;;
  worker)    cd /app/services/api && exec python -m app.worker ;;
  scheduler) cd /app/services/api && exec python -m app.scheduler ;;
  akountz)   migrate akountz; serve akountz 8002 ;;
  angula)    serve angula 8003 ;;
  gateway)   serve gateway 8080 ;;
  studio)    serve studio 8090 ;;
  migrate)   migrate api; migrate akountz ;;
  *)
    echo "usage: pawabase api|worker|scheduler|akountz|angula|gateway|studio|migrate" >&2
    exit 64 ;;
esac
