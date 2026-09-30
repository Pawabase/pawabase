/* OmniStore demo storefront.
 *
 * Everything here talks to the Pawabase gateway: the catalog is read from
 * /rest/v1, and the cart, coupon, quote and checkout go through the blueprint's
 * own /storefront routes — the same flows the API runs for anybody else.
 *
 *   config.js  → gateway, store slug and the publishable key (generated)
 *   localStorage → the cart token, and the secret key if staff unlock the
 *                  back office. Nothing is stored anywhere else.
 */
(() => {
  "use strict";

  const cfg = window.OMNISTORE || {};
  const GATEWAY = (cfg.gateway || "http://localhost:8080").replace(/\/$/, "");
  const STORE = cfg.store || "omnistore-demo";
  const API = `${GATEWAY}/rest/v1`;
  const KEY = cfg.publishableKey || "";
  const SECRET_STORE = "omnistore.secret";
  const TOKEN_STORE = "omnistore.cart";

  const money = (minor, currency = "NGN") =>
    new Intl.NumberFormat("en-NG", { style: "currency", currency, maximumFractionDigits: 0 })
      .format((minor || 0) / 100);

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, props = {}, ...kids) => {
    const node = Object.assign(document.createElement(tag), props);
    for (const kid of kids.flat()) {
      if (kid != null) node.append(kid.nodeType ? kid : String(kid));
    }
    return node;
  };

  let toastTimer;
  function toast(message, bad = false) {
    const box = $("#toast");
    box.textContent = message;
    box.classList.toggle("bad", bad);
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, bad ? 6000 : 3000);
  }

  /* ── the API client ──────────────────────────────────────────────────── */

  async function call(path, { method = "GET", body, key = KEY, query } = {}) {
    const url = new URL(API + path, GATEWAY);
    for (const [k, v] of Object.entries(query || {})) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
    const response = await fetch(url, {
      method,
      headers: { apikey: key, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await response.text();
    const data = text ? JSON.parse(text) : null;
    if (!response.ok) {
      const detail = data && data.details ? data.details[0] : null;
      const error = new Error((detail && detail.msg) || (data && data.message) || response.statusText);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  /* ── cart state ──────────────────────────────────────────────────────── */

  let cart = null;          // the server's cart row
  let lines = [];          // its lines, joined with product detail
  let quote = null;        // shipping options + tax for the chosen country
  let rate = null;         // the chosen shipping rate
  let lastEmail = "";      // remembered so a returning shopper is not retyped to

  const cartToken = {
    get: () => localStorage.getItem(TOKEN_STORE),
    set: (token) => token ? localStorage.setItem(TOKEN_STORE, token) : localStorage.removeItem(TOKEN_STORE),
  };

  /* ── the basket ──────────────────────────────────────────────────────── */

  function renderCount() {
    $("#cart-count").textContent = String(cart ? cart.item_count : 0);
  }

  async function loadCart() {
    const token = cartToken.get();
    if (!token) { cart = null; lines = []; renderCount(); return; }
    try {
      const view = await call(`/storefront/cart/${encodeURIComponent(token)}`, { query: { store: STORE } });
      cart = view.cart;
      lines = view.lines || [];
    } catch (error) {
      if (error.status === 404) { cartToken.set(null); cart = null; lines = []; }
      else throw error;
    }
    renderCount();
  }

  function renderCart() {
    const box = $("#cart-lines");
    box.replaceChildren();
    if (!lines.length) {
      box.append(el("p", { className: "empty" }, "Your basket is empty."));
      $("#to-checkout").disabled = true;
      return;
    }
    for (const line of lines) {
      const step = async (quantity) => {
        try {
          const result = await call("/storefront/cart/lines", {
            method: "POST",
            query: { store: STORE },
            body: { line_id: line.id, quantity },
          });
          lines = result.lines || [];
          cart = result.cart;
          renderCount();
          renderCart();
        } catch (error) {
          toast(error.message, true);
        }
      };
      box.append(el("div", { className: "line" },
        el("img", { src: line.image_url || "", alt: "" }),
        el("div", { className: "grow" },
          el("strong", {}, line.product_title),
          el("span", {}, `${line.variant_title || ""} · ${line.sku || ""}`)),
        el("div", { className: "qty" },
          el("button", { "aria-label": "one fewer", onclick: () => step(Math.max(0, line.quantity - 1)) }, "−"),
          el("output", {}, String(line.quantity)),
          el("button", { "aria-label": "one more", onclick: () => step(line.quantity + 1) }, "+")),
        el("span", { className: "price" }, money(line.total_minor, cart.currency))));
    }
    $("#to-checkout").disabled = false;
    if (quote) renderQuote();
  }

  async function refreshQuote() {
    const token = cartToken.get();
    if (!token || !cart) { quote = null; rate = null; return; }
    try {
      quote = await call("/storefront/quote", {
        query: { store: STORE, cart_token: token, country: country() },
      });
      rate = quote.shipping_options[0] || null;
      renderQuote();
    } catch (error) {
      quote = null;
    }
  }

  const country = () => ($("#checkout-form").country.value || "NG").toUpperCase();

  function renderQuote() {
    const list = $("#quote");
    if (!quote || !cart) { list.replaceChildren(); return; }
    const options = el("div", { className: "row" },
      el("label", { for: "rate" }, "Delivery"),
      el("select", {
        id: "rate",
        onchange: (event) => {
          rate = quote.shipping_options.find((o) => String(o.id) === event.target.value) || null;
          renderQuote();
        },
      }, quote.shipping_options.map((option) => el("option", {
        value: option.id,
        selected: rate && rate.id === option.id,
      }, `${option.name} — ${money(option.price_minor, cart.currency)}`))));

    const rows = [
      ["Subtotal", money(cart.subtotal_minor, cart.currency)],
      ...(cart.discount_minor ? [["Coupon " + (cart.coupon_code || ""), `− ${money(cart.discount_minor, cart.currency)}`]] : []),
      ["Delivery", rate ? shippingCost() : "—"],
      ["Tax", money(quote.tax_minor, cart.currency)],
    ];
    const total = cart.subtotal_minor - cart.discount_minor + shippingCost() + quote.tax_minor;
    list.replaceChildren(
      options,
      ...rows.flatMap(([label, value]) => [el("dt", {}, label), el("dd", {}, value)]),
      el("dt", { className: "grand" }, "Total"),
      el("dd", { className: "grand" }, money(total, cart.currency)),
    );
    $("#quote-note").textContent = rate
      ? `${rate.delivery_estimate || "Standard delivery"} · ${quote.weight_grams} g`
      : "This store does not ship to that country yet.";
  }

  const shippingCost = () => {
    if (!rate || !cart) return 0;
    const net = cart.subtotal_minor - cart.discount_minor;
    if (rate.free_over_minor > 0 && net >= rate.free_over_minor) return 0;
    return rate.price_minor;
  };


  /** The five views: shop, cart, checkout, done, orders. */
  function show(view) {
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view-${view}`));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.view === view));
    if (view === "cart") { renderCart(); refreshQuote(); }
    if (view === "checkout") { $("#checkout-form").email.value = lastEmail; }
    if (view === "orders") loadOffice();
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  /* ── the catalog ─────────────────────────────────────────────────────── */

  async function loadCatalog() {
    const [products, variants, media, banners] = await Promise.all([
      call("/products", { query: { store_id: STORE, status: "active", sort: "id", limit: 60 } }),
      call("/product_variants", { query: { store_id: STORE, status: "active", sort: "id", limit: 200 } }),
      call("/product_media", { query: { store_id: STORE, sort: "id", limit: 200 } }),
      call("/banners", { query: { store_id: STORE, is_published: true, sort: "position", limit: 5 } }),
    ]);

    const byProduct = new Map();
    for (const variant of variants.data) {
      if (!byProduct.has(variant.product_id)) byProduct.set(variant.product_id, []);
      byProduct.get(variant.product_id).push(variant);
    }
    const photo = new Map(media.data.map((m) => [m.product_id, m.url]));

    const grid = $("#products");
    grid.setAttribute("aria-busy", "false");
    grid.replaceChildren();
    for (const product of products.data) {
      const options = byProduct.get(product.id) || [];
      if (!options.length) continue;
      const cheapest = options.reduce((a, b) => (a.price_minor <= b.price_minor ? a : b));
      const soldOut = cheapest.available <= 0 && cheapest.track_inventory;
      const card = el("article", { className: "card" },
        el("img", { src: photo.get(product.id) || "", alt: product.title, loading: "lazy" }),
        el("div", { className: "body" },
          el("h3", {}, product.title),
          el("p", { className: "meta" },
            `${options.length} variant${options.length > 1 ? "s" : ""}`,
            product.rating_count ? ` · ★ ${(product.rating_avg / 100).toFixed(1)} (${product.rating_count})` : ""),
          el("div", { className: "foot" },
            el("span", { className: "price" }, money(cheapest.price_minor)),
            el("button", {
              className: "primary",
              disabled: soldOut,
              onclick: (event) => addToCart(event.currentTarget, cheapest, options),
            }, soldOut ? "Sold out" : "Add to basket"))));
      grid.append(card);
    }
    if (!grid.children.length) grid.append(el("p", { className: "empty" }, "No products are live yet."));

    const strip = $("#banners");
    strip.replaceChildren();
    for (const banner of banners.data) {
      strip.append(el("div", { className: "banner" },
        el("img", { src: banner.image_url, alt: "" }),
        el("div", {},
          el("h2", {}, banner.headline || banner.title),
          el("p", {}, banner.subheadline || ""),
          banner.cta_label ? el("span", { className: "badge" }, banner.cta_label) : null)));
    }
  }

  async function addToCart(button, variant, options) {
    button.disabled = true;
    try {
      const result = await call("/storefront/cart/items", {
        method: "POST",
        query: { store: STORE },
        body: {
          variant_id: variant.id,
          quantity: 1,
          ...(cartToken.get() ? { cart_token: cartToken.get() } : {}),
          ...(lastEmail ? { email: lastEmail } : {}),
        },
      });
      cartToken.set(result.cart_token);
      cart = result.cart;
      lines = result.lines || [];
      renderCount();
      toast(`${variant.title} added`);
      if (options.length > 1) toast(`${options.length} variants available — the cheapest was added`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }


  /* ── coupons ─────────────────────────────────────────────────────────── */

  async function applyCoupon(code) {
    const token = cartToken.get();
    if (!token) { toast("Your basket is empty", true); return; }
    try {
      const result = await call("/storefront/cart/discount", {
        method: "POST",
        query: { store: STORE },
        body: { cart_token: token, code },
      });
      cart = { ...cart, coupon_code: result.coupon.code, discount_minor: result.discount_minor };
      toast(`${result.coupon.title} — ${money(result.discount_minor, cart.currency)} off`);
      renderCart();
      refreshQuote();
    } catch (error) {
      toast(error.message, true);
    }
  }

  /* ── checkout ────────────────────────────────────────────────────────── */

  async function placeOrder(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const field = (name) => form.elements[name].value.trim();
    const button = $("#place-order");
    button.disabled = true;
    try {
      const result = await call("/storefront/checkout", {
        method: "POST",
        query: { store: STORE },
        body: {
          cart_token: cartToken.get(),
          email: field("email"),
          rate_id: rate ? rate.id : 0,
          idempotency_key: (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()),
          phone: field("phone") || undefined,
          customer_note: field("customer_note") || undefined,
          shipping_address: {
            first_name: field("first_name"),
            last_name: field("last_name"),
            line1: field("line1"),
            city: field("city"),
            country: field("country").toUpperCase(),
            phone: field("phone") || undefined,
          },
        },
      });
      cartToken.set(null);
      lastEmail = order.email || lastEmail;
      cart = null; lines = []; quote = null; rate = null;
      renderCount();
      const order = result.order;
      $("#order-ref").textContent = order.reference;
      $("#order-summary").textContent =
        `${money(order.total_minor, order.currency)} · ${order.shipping_method} · ` +
        (result.payment_mode === "gateway" ? "pay online" : "pay on collection or transfer");
      show("done");
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
    }
  }

  /* ── the back office ─────────────────────────────────────────────────── */

  const secret = {
    get: () => localStorage.getItem(SECRET_STORE),
    set: (key) => key ? localStorage.setItem(SECRET_STORE, key) : localStorage.removeItem(SECRET_STORE),
  };

  async function loadOffice() {
    const key = secret.get();
    $("#key-status").textContent = key ? "Unlocked." : "Locked.";
    $("#office").hidden = !key;
    if (!key) return;
    try {
      const [orders, levels, stats] = await Promise.all([
        call("/orders", { key, query: { store_id: STORE, sort: "-placed_at", limit: 8 } }),
        call("/stock_levels", { key, query: { store_id: STORE, sort: "id", limit: 200 } }),
        call("/inventory_ledger", { key, query: { store_id: STORE, sort: "-id", limit: 8 } }),
      ]);
      const paid = orders.data.filter((o) => o.payment_status === "paid");
      const revenue = paid.reduce((sum, o) => sum + (o.total_minor - o.refunded_minor), 0);
      const low = levels.data
        .map((level) => ({ ...level, available: level.on_hand - level.reserved }))
        .filter((level) => level.available <= level.reorder_point);

      $("#dash").replaceChildren(...[
        ["Orders", orders.data.length], ["Paid", paid.length],
        ["Revenue", money(revenue)], ["Low stock", low.length],
      ].map(([label, value]) => el("div", { className: "stat" },
        el("b", {}, String(value)), el("span", {}, label))));

      $("#orders").replaceChildren(table(
        ["#", "Reference", "Status", "Payment", "Total", "Placed"],
        orders.data.map((order) => [
          String(order.number), order.reference,
          badge(order.status), badge(order.payment_status),
          money(order.total_minor, order.currency),
          (order.placed_at || "").slice(0, 10),
        ])));

      $("#low-stock").replaceChildren(low.length
        ? table(["SKU", "Warehouse", "Available", "Reorder at"],
            low.map((level) => [String(level.id), String(level.warehouse_id),
              String(level.available), String(level.reorder_point)]))
        : el("p", { className: "empty" }, "Nothing is running out."));

      if (stats.data.length) {
        $("#low-stock").append(el("p", { className: "muted" },
          "Last movements: " + stats.data.slice(0, 3)
            .map((row) => `${row.reason} ${row.delta > 0 ? "+" : ""}${row.delta}`).join(", ")));
      }
    } catch (error) {
      $("#key-status").textContent = error.status === 401
        ? "That key was refused."
        : error.message;
      secret.set(null);
      $("#office").hidden = true;
    }
  }

  const badge = (value) =>
    el("span", {
      className: "badge" + (/(paid|fulfilled|delivered|approved|active)/.test(value || "") ? " good"
        : /(cancelled|failed|refunded|spam|rejected)/.test(value || "") ? " bad" : ""),
    }, value || "—");

  function table(headers, rows) {
    return el("table", {},
      el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.map((row) =>
        el("tr", {}, row.map((cell) => el("td", {}, cell))))));
  }

  /* ── wiring ──────────────────────────────────────────────────────────── */

  function wire() {
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => show(tab.dataset.view));
    });
    $("#to-checkout").addEventListener("click", () => { show("checkout"); refreshQuote(); });
    $("#back-to-shop").addEventListener("click", () => show("shop"));
    $("#checkout-form").addEventListener("submit", placeOrder);
    $("#checkout-form").elements.country.addEventListener("change", refreshQuote);
    $("#coupon-form").addEventListener("submit", (event) => {
      event.preventDefault();
      applyCoupon($("#coupon").value.trim());
    });
    $("#coupon-clear").addEventListener("click", () => applyCoupon(cart && cart.coupon_code));
    $("#key-form").addEventListener("submit", (event) => {
      event.preventDefault();
      secret.set($("#secret-key").value.trim());
      $("#secret-key").value = "";
      loadOffice();
    });
    $("#key-forget").addEventListener("click", () => { secret.set(null); loadOffice(); });
  }

  async function boot() {
    $("#cfg-gateway").textContent = GATEWAY;
    $("#cfg-store").textContent = STORE;
    $("#cfg-key").textContent = KEY ? `${KEY.slice(0, 10)}…${KEY.slice(-4)}` : "not set";
    $("#status").textContent = "";
    wire();
    try {
      await Promise.all([loadCatalog(), loadCart()]);
      await refreshQuote();
      if (!KEY) {
        $("#status").textContent = "no publishable key — run bootstrap.py";
        toast("No publishable key in config.js — run bootstrap.py", true);
      }
    } catch (error) {
      $("#status").textContent = `cannot reach ${GATEWAY}`;
      toast(`${error.message} — is the stack running?`, true);
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
