# Sell4Me Commerce: a Pawabase blueprint

A multi-store commerce backend (storefront, checkout, payments, fulfilment,
refunds, inventory, POS, support, reviews, payouts, analytics) delivered as a
single blueprint: [`commerce.blueprint.json`](commerce.blueprint.json).

| | |
| --- | --- |
| Definitions | 143: 39 resources, 31 flows, 22 custom routes, 16 policies, 15 schemas, 10 mail templates, 3 buckets, 2 transformers, 2 webhooks, 1 inbound hook, 1 subscription, 1 schedule |
| Roles | `platform_admin`, `customer` (given at sign-up), `support_agent` |
| Demo data | store `sell4me-demo` ("Naija Threads", NGN): 8 products, 15 variants, 3 collections, shipping zones, 7.5% VAT, discount codes `WELCOME15`, `NAIRA5000`, `SHIPFREE` |

Everything runs inside Pawabase. There is no local code: the logic is flows
you can open, read and change in Studio.

## 1. Create the project

In Studio, go to **Projects → New project → From blueprint**, choose
`commerce.blueprint.json` and create the project. The confirmation screen shows
two values **once**; copy them:

- the environment's **publishable** and **secret** keys;
- the Paystack secret key is configured after import as the environment secret
  **`PAYSTACK_SECRET_KEY`**. It is read only by the checkout flow and is never
  sent to a browser.

Webhooks (`erp_sync`, `analytics_warehouse`) are imported **switched off**. Point them
at your own URLs before enabling them.

To use the demo store, sign up a merchant and create the organization
**`sell4me-demo`**; the demo rows belong to it (see step 2). For a new store,
call `POST /merchant/stores` instead.

`build.py` regenerates the JSON. Edit it and run `python build.py`, or change
the definitions in Studio and export a new blueprint from the environment's
settings.

## 2. The model your frontend needs to know

- **A store is an organization.** Every store-owned row has `store_id`, which
  is the org slug. Merchants and staff are org members: `owner`, `admin`
  (manager), `member` (operator), `viewer`.
- **Merchant tokens are org-scoped.** Sign in with `org` to act for a store.
  The token carries `org` and `org_role`, and every merchant policy checks
  them. Switching store means requesting a new token with another `org`.
- **Shoppers** are plain users with the `customer` role. Guests can shop
  anonymously; carts and orders are keyed by unguessable tokens.
- **Money is integer minor units** (`*_minor`, e.g. kobo): ₦35,000.00 is
  `3500000`. Format on the client.
- **Refunds need MFA.** The refund route requires an `aal2` token: enrol TOTP,
  then sign in with the code.

```http
POST /auth/v1/token?grant_type=password
apikey: <publishable key>

{ "email": "ada@naijathreads.example", "password": "…", "org": "sell4me-demo" }
```

All requests go to the gateway (`http://localhost:8080` in development) with
`apikey: <publishable key>` and, when signed in,
`Authorization: Bearer <access_token>`. Never ship the secret key to a browser.

## 3. Storefront (shopper-facing)

Read the catalogue with the REST API, anonymously:

```http
GET /rest/v1/products?filter[store_id]=sell4me-demo&sort=-published_at
GET /rest/v1/products/3?expand=variants,images
GET /rest/v1/collections?filter[store_id]=sell4me-demo&expand=products
GET /rest/v1/reviews?filter[product_id]=3          # approved reviews only
GET /rest/v1/pages?filter[store_id]=sell4me-demo   # about, shipping, returns
```

The public never sees `cost_minor` or stock reservations. Filters must use
`filter[field]=value`.

Carts, checkout and orders are custom routes under `/rest/v1`:

| Method & path | Body | Notes |
| --- | --- | --- |
| `POST /storefront/cart/items` | `store_id`, `variant_id`, `quantity`, `cart_token?` | Omit `cart_token` to start a cart; keep the returned token |
| `POST /storefront/cart/update` | `cart_token`, `item_id`, `quantity` | `quantity: 0` removes the line |
| `GET /storefront/cart/{token}` | | Lines, discount and totals |
| `POST /storefront/cart/discount` | `cart_token`, `code` | 422 with a reason if the code doesn't apply |
| `DELETE /storefront/cart/{token}/discount` | | |
| `POST /storefront/checkout` | `cart_token`, `email`, `shipping_address`, `billing_address?`, `phone?`, `note?` | Returns `order` and `payment.reference`; reserves stock |
| `GET /storefront/orders/{reference}?email=` | | Guest order tracking |
| `GET /storefront/recover/{token}` | | Link target of the abandoned-cart email |
| `POST /storefront/support` | `store_id`, `name`, `email`, `subject`, `message`, `order_number?` | Returns a ticket token for replies |
| `POST /storefront/support/{token}/messages` | `message` | Reopens a closed ticket |
| `POST /storefront/reviews` | `product_id`, `rating`, `title?`, `body?` | Signed-in shoppers; one per product; `verified_purchase` if they have a paid order |

An address is
`{ first_name, last_name, line1, city, country, company?, line2?, province?, postal_code?, phone? }`.
Order totals are subtotal − discount + shipping + 7.5% VAT; shipping is free
above the zone's threshold.

**Payment.** Checkout initializes Paystack server-side and returns
`payment.authorization_url`, `payment.access_code`, and the unique
`payment.reference`. Redirect the shopper to `authorization_url` (or use the
access code with the Paystack client integration); never initialize a
transaction from the browser because that would expose the secret key. In
**Operate → Secrets**, set `PAYSTACK_SECRET_KEY`. In **Automate → Inbound hooks →
Paystack**, set its **Secret** to the same Paystack secret key, then register
the displayed hook URL in the Paystack dashboard. The hook verifies
`x-paystack-signature` as HMAC-SHA512 before the payment flow is queued.

Paystack's `charge.success` callback has this shape:

```json
{ "event": "charge.success",
  "data": { "reference": "<payment.reference>", "amount": 3762500, "fees": 15000, "id": "ch_123", "channel": "card" } }
```

The callback marks the payment captured and the order paid, commits stock,
updates the customer's totals, credits the store's ledger and emails the
confirmation. Duplicate notifications are ignored. Use the server-side
Paystack verification endpoint as a recovery path for an interrupted browser
return; do not mark an order paid solely because a client reports success. Poll
`/storefront/orders/{reference}` or subscribe to realtime for the result.

## 4. Merchant dashboard

Sign in with `org`. Store data is readable through REST, filtered to the
active store by policy:

```http
GET /rest/v1/orders?sort=-id&filter[payment_status]=paid
GET /rest/v1/customers?sort=-total_spent_minor
GET /rest/v1/tickets?filter[status]=open&expand=messages
GET /rest/v1/notifications?sort=-id
```

Actions and composite views:

| Method & path | Who | Body |
| --- | --- | --- |
| `POST /merchant/stores` | any signed-in user | `name`, `currency`, `country`, `email`, `timezone?`: creates the org, the store and defaults |
| `GET /merchant/dashboard` | any member | none; returns 30-day sales, today, pipeline, top products, low stock, abandoned carts, balance, customers. Cached 30 s |
| `GET /merchant/inventory` | any member | none; returns variants with stock, reserved, available and cost |
| `GET /merchant/orders/{id}` | any member | none; returns the order with lines, timeline, payments and refunds |
| `POST /orders/{id}/mark-paid` | member+ | `method`, `reference?` (bank transfer, cash on delivery) |
| `POST /orders/{id}/fulfil` | member+ | `carrier?`, `tracking_number?`, `tracking_url?`, `notify_customer?` |
| `POST /orders/{id}/cancel` | admin+ | `reason`, `note?`: releases stock and voids pending payment |
| `POST /orders/{id}/refunds` | admin+ with MFA | `amount_minor`, `reason`, `note?`, `restock?` |
| `POST /inventory/adjust` | member+ | `variant_id`, `delta`, `reason`, `note?` |
| `POST /support/tickets/{id}/reply` | member+ | `message`, `close?` |
| `POST /pos/sales` | member+ | `session_id`, `method`, `lines: [{variant_id, quantity}]`, `email?` |

Catalogue management (products, variants, images, collections, discounts,
campaigns, shipping, pages, themes, POS devices and sessions) is plain REST CRUD
on the matching resources. Editors (`member`+) create and update; managers
(`admin`+) delete.

**Uploads**: `product-media` and `store-assets` are public buckets. Objects
must live under the store's folder: `<store_id>/products/…`.

**Realtime**: members can subscribe to `store:<store_id>` for live order,
stock and notification events (presence and 100-message history included).

## 5. Background automation

| Trigger | Flow | What it does |
| --- | --- | --- |
| variant updated | `low_stock_alert` | Notifies and emails when a variant falls to its low-stock threshold |
| `payment.captured` → `order.paid` | `capture_payment`, `order_paid` | Ledger, customer stats, confirmation email |
| every 15 min | `abandoned_cart_sweep` | Emails recovery links for idle carts |
| review updated or deleted | `review_rollup` | Keeps `rating_avg` and `rating_count` on products (approval counts) |
| daily 00:10 | `daily_rollup` | Writes `daily_stats` for charts |
| daily 06:00 | `payout_run` | Moves each store's ledger balance into a payout |
| Mondays 08:00 UTC | schedule `weekly_merchant_digest` | Emits `merchant.weekly_digest` for your own digest flow or webhook |

## 6. Operator roles

- `platform_admin` sees and manages every store.
- `support_agent` reads orders, tickets and messages across stores (replying through `/support/tickets/{id}/reply` stays with store staff).

Grant them from Studio → Auth → Users.
