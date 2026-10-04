#!/usr/bin/env bash
# Run every Pawabase service locally on SQLite, no Docker needed.
#
#   scripts/dev.sh            # start everything; Ctrl-C stops it
#   STUDIO_VITE=1 scripts/dev.sh   # Studio loads the front end from `npm run dev`
#
# State lives in ./.dev (delete it to start over). Studio is on :8090, the
# gateway on :8080.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="${PAWABASE_DEV_DIR:-$ROOT/.dev}"
mkdir -p "$STATE/logs"

export SILLO_ENV_FILE=""
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PAWABASE_APP_ENV="${PAWABASE_APP_ENV:-local}"
export PAWABASE_INTERNAL_SECRET="${PAWABASE_INTERNAL_SECRET:-dev-internal-secret-change-me-please}"
export PAWABASE_JWT_MASTER_SECRET="${PAWABASE_JWT_MASTER_SECRET:-dev-jwt-master-secret-change-me-please}"
export PAWABASE_MASTER_KEY="${PAWABASE_MASTER_KEY:-dev-master-key-change-me-please-0000}"
export PAWABASE_DB_GENERATE_SCHEMAS=true
export PAWABASE_PUBLIC_URL="http://127.0.0.1:8080"
export PAWABASE_PUBLIC_GATEWAY_URL="http://127.0.0.1:8080"
export PAWABASE_INLINE_SCHEDULER=true
[ -n "${STUDIO_VITE:-}" ] && export PAWABASE_VITE_DEV=true

if [ -z "${STUDIO_VITE:-}" ] && [ ! -f "$ROOT/studio/frontend/dist/.vite/manifest.json" ]; then
  echo "Building Studio's front end…"
  (cd "$ROOT/studio/frontend" && npm install --no-audit --no-fund && npm run build)
fi

pids=()
start() {
  local name=$1 port=$2
  shift 2
  (cd "$ROOT/$name" && env "$@" uv run uvicorn app.main:app --host 127.0.0.1 --port "$port" --log-level warning) \
    >"$STATE/logs/$name.log" 2>&1 &
  pids+=($!)
  echo "  $name → http://127.0.0.1:$port   (log: .dev/logs/$name.log)"
}
trap 'kill "${pids[@]}" 2>/dev/null; wait' EXIT INT TERM

echo "Starting Pawabase:"
start api 8001 PAWABASE_DATABASE_URL="sqlite://$STATE/api.db" PAWABASE_DEFAULT_DATA_URL="sqlite://$STATE/data/{env}.db" PAWABASE_STORAGE_ROOT="$STATE/objects" PAWABASE_CODE_PATH="$ROOT/code"
start akountz 8002 PAWABASE_DATABASE_URL="sqlite://$STATE/akountz.db"
start angula 8003
start gateway 8080
start studio 8090
echo "Studio: http://127.0.0.1:8090"
wait
