#!/usr/bin/env bash
# Smoke-test a running Pawabase: Studio opens without sign-in, an environment
# created through Studio, and data written and read through the gateway with
# its key.
#
#   scripts/smoke.sh [gateway url] [studio url]
set -euo pipefail
GATEWAY=${1:-http://127.0.0.1:8080}
STUDIO=${2:-http://127.0.0.1:8090}
JAR=$(mktemp)
ENV="smoke$(date +%s)"
trap 'rm -f "$JAR"' EXIT

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
xsrf() { awk '$6 == "XSRF-TOKEN" { print $7 }' "$JAR" | tail -1; }
studio() {  # studio METHOD PATH [JSON]
  curl -sS -f -b "$JAR" -c "$JAR" -X "$1" "$STUDIO$2" \
    -H "content-type: application/json" -H "X-XSRF-TOKEN: $(xsrf)" ${3:+-d "$3"}
}

echo "gateway status"
curl -sS -f "$GATEWAY/v1/status" | json 'd["ok"]' | grep -q True

echo "studio opens without sign-in"
code=$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" "$STUDIO/")
[ "$code" = 200 ] || [ "$code" = 302 ] || { echo "studio answered $code"; exit 1; }
studio GET /studio/api/platform/runtime | json 'd["environments"][0]["name"]' | grep -q .

echo "environment $ENV"
KEY=$(studio POST /studio/api/platform/envs "{\"name\": \"$ENV\"}" | json 'd["keys"]["publishable"]')
studio POST "/studio/api/platform/envs/$ENV/resources" \
  '{"name": "notes", "fields": [{"name": "text", "type": "string", "required": true}], "operations": {"list": {"enabled": true, "policy": "public"}, "create": {"enabled": true, "policy": "public"}}}' >/dev/null
studio POST "/studio/api/platform/envs/$ENV/resources/notes/migrate" >/dev/null

echo "data through the gateway"
curl -sS -f -X POST "$GATEWAY/rest/v1/notes" -H "apikey: $KEY" -H "content-type: application/json" -d '{"text": "hello"}' >/dev/null
curl -sS -f "$GATEWAY/rest/v1/notes" -H "apikey: $KEY" | json 'd["data"][0]["text"]' | grep -q hello
code=$(curl -sS -o /dev/null -w '%{http_code}' "$GATEWAY/rest/v1/notes")
[ "$code" = 401 ] || { echo "a request without a key answered $code"; exit 1; }

echo "sign-up through the gateway"
curl -sS -f -X POST "$GATEWAY/auth/v1/signup" -H "apikey: $KEY" -H "content-type: application/json" \
  -d '{"email": "smoke@example.com", "password": "Sm0ke!pass"}' | json 'd.get("access_token") or d.get("user")' >/dev/null

echo "smoke test passed"
