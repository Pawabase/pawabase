"""Storefront routes: what a browser or a mobile app calls.

Each route is thin on purpose — a policy decides who may call it, a schema
validates the body, and a flow does the work. Plain CRUD is already served by
``/rest/v1`` from the resources themselves; these are the places where *several
things happen together*. The heavy routes are rate limited: a storefront that
hammers the basket endpoint is either broken or hostile.
"""

from __future__ import annotations

from helpers import F, route

SHOP = ["Storefront"]
ACCOUNT = ["Storefront", "Account"]
CONTENT = ["Content", "Storefront"]

ROUTES = [
    route("POST", "/storefront/cart/items", "cart_add", "Add a variant to a basket (opens one when there is no token).",
          "public", "cart_add", tags=SHOP, input_schema="CartAdd", rate={"limit": 120, "window": 60}),
    route("POST", "/storefront/cart/lines", "cart_update", "Change a line's quantity, or drop it with 0.",
          "public", "cart_update", tags=SHOP, input_schema="CartQuantity", rate={"limit": 120, "window": 60}),
    route("GET", "/storefront/cart/{token}", "cart_view", "A basket with its lines and any coupon.",
          "public", "cart_view", tags=SHOP, rate={"limit": 240, "window": 60}, cache=15),
    route("POST", "/storefront/cart/discount", "cart_coupon", "Prove a code and price it onto the basket.",
          "public", "cart_coupon", tags=SHOP, input_schema="CartCode", rate={"limit": 30, "window": 60}),
    route("GET", "/storefront/quote", "cart_quote", "Subtotal, coupon, shipping options and tax for a country.",
          "public", "cart_quote", tags=SHOP, rate={"limit": 60, "window": 60}, cache=20),
    route("POST", "/storefront/checkout", "checkout", "Price the basket, hold the stock and open the order.",
          "public", "checkout", tags=SHOP, input_schema="CheckoutInput", rate={"limit": 20, "window": 60}),
    route("POST", "/storefront/returns", "request_return", "Ask to send an order back.",
          "authenticated", "request_return", tags=SHOP, input_schema="ReturnRequest",
          rate={"limit": 10, "window": 60}),
]
