#!/usr/bin/env bash
# Run Pawabase locally on SQLite, no Docker needed: the server, and Studio beside it.
#
#   scripts/dev.sh                 # start both; Ctrl-C stops them
#   STUDIO_VITE=1 scripts/dev.sh   # Studio loads the front end from `npm run dev`
#
# State lives in ./.dev (delete it to start over). The public API is on :8080, Studio on :8090; the server's internal listener (what Studio
# manages it through) is on 127.0.0.1:8001.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="${PAWABASE_DEV_DIR:-$ROOT/.dev}"
FRONTEND="$ROOT/studio/frontend"
mkdir -p "$STATE"

export SILLO_ENV_FILE=""
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PAWABASE_APP_ENV="${PAWABASE_APP_ENV:-local}"
export PAWABASE_INTERNAL_SECRET="${PAWABASE_INTERNAL_SECRET:-dev-internal-secret-change-me-please}"
export PAWABASE_JWT_MASTER_SECRET="${PAWABASE_JWT_MASTER_SECRET:-dev-jwt-master-secret-change-me-please}"
export PAWABASE_MASTER_KEY="${PAWABASE_MASTER_KEY:-dev-master-key-change-me-please-0000}"
export PAWABASE_DATABASE_URL="${PAWABASE_DATABASE_URL:-sqlite://$STATE/pawabase.db}"
export PAWABASE_DEFAULT_DATA_URL="${PAWABASE_DEFAULT_DATA_URL:-sqlite://$STATE/data/{env}.db}"
export PAWABASE_STORAGE_ROOT="${PAWABASE_STORAGE_ROOT:-$STATE/objects}"
export PAWABASE_CODE_PATH="${PAWABASE_CODE_PATH:-$ROOT/code}"
export PAWABASE_DB_GENERATE_SCHEMAS=true
export PAWABASE_PUBLIC_URL="http://127.0.0.1:8080"
export PAWABASE_PUBLIC_GATEWAY_URL="http://127.0.0.1:8080"
export PAWABASE_SERVER_URL="http://127.0.0.1:8001"
[ -n "${STUDIO_VITE:-}" ] && export PAWABASE_VITE_DEV=true

if [ -z "${STUDIO_VITE:-}" ] && [ ! -f "$FRONTEND/dist/.vite/manifest.json" ]; then
  echo "Building Studio's front end…"
  (cd "$FRONTEND" && npm install --no-audit --no-fund && npm run build)
fi

cd "$ROOT"
pids=()
trap 'kill "${pids[@]}" 2>/dev/null; wait' EXIT INT TERM
echo "Starting Pawabase:  API http://127.0.0.1:8080   Studio http://127.0.0.1:8090"
uv run python -m pawabase_server serve & pids+=($!)
uv run python -m studio & pids+=($!)
wait
