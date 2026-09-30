#!/usr/bin/env bash
# Prove every call the web UI makes actually works, before opening a browser.
#
#   ./smoke.sh <secret-key>          # secret key unlocks the back-office calls
#
# The publishable key and the store slug come from config.js, which bootstrap.py
# generated. Exits non-zero on the first failure.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
gateway="$(python3 -c "import json,re,sys;print(re.search(r'\"gateway\": \"(.*?)\"', open(sys.argv[1]).read()).group(1))" "$here/config.js")"
store="$(python3 -c "import json,re,sys;print(re.search(r'\"store\": \"(.*?)\"', open(sys.argv[1]).read()).group(1))" "$here/config.js")"
publishable="$(grep -o 'pb_pk_[A-Za-z0-9_-]*' "$here/config.js")"
secret="${1:-}"

api="$gateway/rest/v1"
body="$(mktemp)"; trap 'rm -f "$body"' EXIT
failures=0

check() { # check <label> <expected> <curl args...>
  local label="$1" expect="$2"; shift 2
  local got
  got="$(curl -s -o "$body" -w '%{http_code}' "$@")"
  if [ "$got" = "$expect" ]; then
    printf '  \033[32mok\033[0m   %-22s %s\n' "$label" "$got"
  else
    printf '  \033[31mFAIL\033[0m %-22s got %s, wanted %s — %s\n' "$label" "$got" "$expect" "$(head -c 160 "$body")"
    failures=$((failures + 1))
  fi
}

json() { python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(eval(sys.argv[2],{'d':d}))" "$body" "$1"; }

echo "storefront — $gateway · $store"
check "catalog: products"   200 -H "apikey: $publishable" "$api/products?store_id=$store&status=active&sort=id&limit=60"
check "catalog: variants"   200 -H "apikey: $publishable" "$api/product_variants?store_id=$store&status=active&sort=id&limit=200"
check "catalog: media"      200 -H "apikey: $publishable" "$api/product_media?store_id=$store&sort=id&limit=200"
check "catalog: banners"    200 -H "apikey: $publishable" "$api/banners?store_id=$store&is_published=true&sort=position&limit=5"

check "cart: add"           200 -X POST -H "apikey: $publishable" -H 'Content-Type: application/json' \
  -d '{"variant_id":1,"quantity":2,"email":"smoke@example.com"}' "$api/storefront/cart/items?store=$store"
token="$(json 'd["cart_token"]')"
line="$(json 'd["lines"][0]["id"]')"
echo "       cart $token, first line $line"

check "cart: view"          200 -H "apikey: $publishable" "$api/storefront/cart/$token?store=$store"
check "cart: change line"   200 -X POST -H "apikey: $publishable" -H 'Content-Type: application/json' \
  -d "{\"line_id\":$line,\"quantity\":3}" "$api/storefront/cart/lines?store=$store"
check "cart: coupon"        200 -X POST -H "apikey: $publishable" -H 'Content-Type: application/json' \
  -d "{\"cart_token\":\"$token\",\"code\":\"WELCOME15\"}" "$api/storefront/cart/discount?store=$store"
check "cart: quote"         200 -H "apikey: $publishable" "$api/storefront/quote?store=$store&cart_token=$token&country=NG"
check "cart: checkout"      200 -X POST -H "apikey: $publishable" -H 'Content-Type: application/json' \
  -d "{\"cart_token\":\"$token\",\"email\":\"smoke@example.com\",\"rate_id\":1,\"shipping_address\":{\"first_name\":\"Ada\",\"last_name\":\"Obi\",\"line1\":\"12 Allen Ave\",\"city\":\"Ikeja\",\"country\":\"NG\"}}" \
  "$api/storefront/checkout?store=$store"
echo "       order $(json 'd["order"]["reference"]') for $(json 'd["order"]["total_minor"]') minor units"

echo "policies — the storefront key must not reach staff data"
check "orders: refused"     401 -H "apikey: $publishable" "$api/orders?store_id=$store"

if [ -n "$secret" ]; then
  echo "back office — secret key"
  check "orders"             200 -H "apikey: $secret" "$api/orders?store_id=$store&sort=-placed_at&limit=8"
  check "stock levels"       200 -H "apikey: $secret" "$api/stock_levels?store_id=$store&sort=id&limit=200"
  check "inventory ledger"   200 -H "apikey: $secret" "$api/inventory_ledger?store_id=$store&sort=-id&limit=8"
else
  echo "back office — skipped (pass the secret key as \$1)"
fi

if [ "$failures" -eq 0 ]; then
  echo -e "\n\033[32mall calls answered as expected\033[0m"
else
  echo -e "\n\033[31m$failures call(s) failed\033[0m"
fi
exit "$failures"
