#!/usr/bin/env bash
# Smoke-test a running Pawabase: Studio sign-in, a project created through
# Studio, and data written and read through the gateway with its key.
#
#   scripts/smoke.sh <admin email> <admin password> [gateway url] [studio url]
set -euo pipefail
EMAIL=$1 PASSWORD=$2
GATEWAY=${3:-http://127.0.0.1:8080}
STUDIO=${4:-http://127.0.0.1:8090}
JAR=$(mktemp)
REF="smoke$(date +%s)"
trap 'rm -f "$JAR"' EXIT

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
xsrf() { awk '$6 == "XSRF-TOKEN" { print $7 }' "$JAR" | tail -1; }
studio() {  # studio METHOD PATH [JSON]
  curl -sS -f -b "$JAR" -c "$JAR" -X "$1" "$STUDIO$2" \
    -H "content-type: application/json" -H "X-XSRF-TOKEN: $(xsrf)" ${3:+-d "$3"}
}

echo "gateway status"
curl -sS -f "$GATEWAY/v1/status" | json 'd["ok"]' | grep -q True

echo "studio sign-in"
curl -sS -f -o /dev/null -b "$JAR" -c "$JAR" "$STUDIO/login"
code=$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" -X POST "$STUDIO/login" \
  -H "content-type: application/json" -H "X-XSRF-TOKEN: $(xsrf)" \
  -d "{\"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}")
[ "$code" = 302 ] || [ "$code" = 303 ] || { echo "sign-in answered $code"; exit 1; }
studio GET /studio/api/platform/projects >/dev/null

echo "project $REF"
KEY=$(studio POST /studio/api/platform/projects "{\"ref\": \"$REF\", \"name\": \"Smoke\", \"environments\": [\"main\"]}" \
  | json 'd["keys"]["main"]["publishable"]')
studio POST "/studio/api/platform/projects/$REF/envs/main/resources" \
  '{"name": "notes", "fields": [{"name": "text", "type": "string", "required": true}], "operations": {"list": {"enabled": true, "policy": "public"}, "create": {"enabled": true, "policy": "public"}}}' >/dev/null
studio POST "/studio/api/platform/projects/$REF/envs/main/resources/notes/migrate" >/dev/null

echo "data through the gateway"
curl -sS -f -X POST "$GATEWAY/rest/v1/notes" -H "apikey: $KEY" -H "content-type: application/json" -d '{"text": "hello"}' >/dev/null
curl -sS -f "$GATEWAY/rest/v1/notes" -H "apikey: $KEY" | json 'd["data"][0]["text"]' | grep -q hello
code=$(curl -sS -o /dev/null -w '%{http_code}' "$GATEWAY/rest/v1/notes")
[ "$code" = 401 ] || { echo "a request without a key answered $code"; exit 1; }

echo "sign-up through the gateway"
curl -sS -f -X POST "$GATEWAY/auth/v1/signup" -H "apikey: $KEY" -H "content-type: application/json" \
  -d '{"email": "smoke@example.com", "password": "Sm0ke!pass"}' | json 'd.get("access_token") or d.get("user")' >/dev/null

echo "smoke test passed"
