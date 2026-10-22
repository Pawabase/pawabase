// Prebuilt templates. Each is an ordinary editor document, so every word,
// block and colour can be changed in the visual editor after it is chosen.

// ── building blocks ───────────────────────────────────────────────────

const t = (text, marks) => ({ type: "text", text, ...(marks ? { marks } : {}) });
const bold = (text) => t(text, [{ type: "bold" }]);
const small = (text) => t(text, [{ type: "textStyle", attrs: { color: "#6b7280", fontSize: "13px" } }]);
const link = (text, href) => t(text, [{ type: "link", attrs: { href } }]);
const v = (name, fallback = "", marks) => ({ type: "variable", attrs: { name, fallback }, ...(marks ? { marks } : {}) });
const vb = (name, fallback = "") => v(name, fallback, [{ type: "bold" }]);
const vs = (name, fallback = "") => v(name, fallback, [{ type: "textStyle", attrs: { color: "#6b7280", fontSize: "13px" } }]);
const parts = (items) => items.map((x) => (typeof x === "string" ? t(x) : x));
const p = (...items) => ({ type: "paragraph", content: parts(items) });
const pa = (align, ...items) => ({ type: "paragraph", attrs: { textAlign: align }, content: parts(items) });
const h = (level, ...items) => ({ type: "heading", attrs: { level }, content: parts(items) });
const ha = (level, align, ...items) => ({ type: "heading", attrs: { level, textAlign: align }, content: parts(items) });
const button = (text, href, attrs = {}) => ({ type: "button", attrs: { text, href, ...attrs } });
const hr = (attrs = {}) => ({ type: "horizontalRule", attrs: { color: "", thickness: 1, spacing: 24, ...attrs } });
const spacer = (height = 16) => ({ type: "spacer", attrs: { height } });
const section = (attrs, ...content) => ({ type: "section", attrs: { background: "#f6f6f8", padding: 24, radius: 10, borderColor: "", align: "", ...attrs }, content });
const columns = (...cols) => ({ type: "columns", attrs: { gap: 16, verticalAlign: "top" }, content: cols.map((content) => ({ type: "column", content })) });
const showIf = (expression, ...content) => ({ type: "conditional", attrs: { expression, mode: "if" }, content });
const repeat = (list, item, ...content) => ({ type: "repeat", attrs: { list, item }, content });
const list = (...items) => ({ type: "bulletList", content: items.map((item) => ({ type: "listItem", content: [p(...[].concat(item))] })) });
const doc = (...content) => ({ type: "doc", content });

const brand = () => p(vb("app.name", "Acme"));
const footer = (...extra) => [
  hr({ spacing: 28 }),
  p(small("Sent by "), vs("app.name", "Acme"), small(" · "), vs("app.address", "")),
  ...extra,
];

const APP = [
  { name: "app.name", description: "Your product's name", sample: "Northwind" },
  { name: "app.address", description: "Your postal address, for the footer", sample: "12 Marina Road, Lagos" },
  { name: "app.support_email", description: "Where people get help", sample: "help@northwind.com" },
];

// ── the templates ─────────────────────────────────────────────────────

export const STARTERS = [
  {
    key: "welcome", name: "Welcome", category: "onboarding", description: "Greet a new account and point to the first steps.",
    subject: "Welcome to {{ app.name }}, {{ user.first_name }}", preheader: "Three quick steps to get you started.",
    variables: [...APP, { name: "user.first_name", description: "Their first name", sample: "Ada" }, { name: "dashboard_url", description: "Where to start", sample: "https://app.northwind.com" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "Welcome aboard, ", v("user.first_name", "there"), " 👋"),
      p("We're glad you're here. Your account is ready, and these three steps take about five minutes:"),
      section({}, p(bold("1. "), "Create your first project"), p(bold("2. "), "Invite your team"), p(bold("3. "), "Connect your app with the API keys")),
      button("Open your dashboard", "{{ dashboard_url }}"),
      p("Questions? Just reply to this email, or write to ", v("app.support_email"), "."),
      ...footer(),
    ),
  },
  {
    key: "verify-email", name: "Email verification", category: "authentication", description: "Confirm a new address with a link.",
    subject: "Confirm your email for {{ app.name }}", preheader: "One click and you're in.",
    variables: [...APP, { name: "user.first_name", sample: "Ada" }, { name: "verify_url", description: "The confirmation link", sample: "https://app.northwind.com/verify?t=…" }, { name: "expires_in", description: "How long the link works", sample: "24 hours" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "Confirm your email"),
      p("Hi ", v("user.first_name", "there"), ", tap the button to confirm this address and finish setting up your account."),
      button("Confirm email", "{{ verify_url }}"),
      p(small("The link works for "), v("expires_in", "24 hours"), small(". If you didn't create an account, you can ignore this email.")),
      ...footer(),
    ),
  },
  {
    key: "password-reset", name: "Password reset", category: "authentication", description: "A reset link with a safety note.",
    subject: "Reset your {{ app.name }} password", preheader: "Your reset link is inside. It expires soon.",
    variables: [...APP, { name: "user.first_name", sample: "Ada" }, { name: "reset_url", sample: "https://app.northwind.com/reset?t=…" }, { name: "expires_in", sample: "60 minutes" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "Reset your password"),
      p("Hi ", v("user.first_name", "there"), ", someone asked to reset the password for your account. If it was you, choose a new one here:"),
      button("Choose a new password", "{{ reset_url }}"),
      p(small("This link works once, for "), v("expires_in", "60 minutes"), small(".")),
      section({ background: "#fff8f0", borderColor: "#fde3c8" }, p(bold("Didn't ask for this?"), " You can safely ignore this email; your password won't change.")),
      ...footer(),
    ),
  },
  {
    key: "magic-link", name: "Magic link", category: "authentication", description: "Passwordless sign-in in one click.",
    subject: "Your sign-in link for {{ app.name }}", preheader: "Tap to sign in. No password needed.",
    variables: [...APP, { name: "link_url", sample: "https://app.northwind.com/magic?t=…" }, { name: "request.device", description: "Where the request came from", sample: "Safari on macOS" }, { name: "expires_in", sample: "15 minutes" }],
    doc: doc(
      brand(), spacer(8),
      ha(1, "center", "Sign in to ", v("app.name", "your account")),
      pa("center", "Tap the button below to sign in. The link works once and expires in ", v("expires_in", "15 minutes"), "."),
      button("Sign in", "{{ link_url }}", { align: "center" }),
      pa("center", small("Requested from "), v("request.device", "a browser"), small(". If this wasn't you, ignore this email.")),
      ...footer(),
    ),
  },
  {
    key: "otp-code", name: "One-time code", category: "authentication", description: "A short code for two-step verification.",
    subject: "{{ code }} is your {{ app.name }} code", preheader: "Enter this code to continue. It expires in 10 minutes.",
    variables: [...APP, { name: "code", description: "The one-time code", sample: "482 915" }, { name: "expires_in", sample: "10 minutes" }],
    doc: doc(
      brand(), spacer(8),
      ha(2, "center", "Your verification code"),
      pa("center", "Enter this code to finish signing in."),
      section({ align: "center", padding: 28 }, pa("center", v("code", "", [{ type: "bold" }, { type: "textStyle", attrs: { fontSize: "34px", color: "#0b0c0e" } }]))),
      pa("center", small("It expires in "), v("expires_in", "10 minutes"), small(". Never share it with anyone, including our team.")),
      ...footer(),
    ),
  },
  {
    key: "team-invite", name: "Team invitation", category: "teams", description: "Invite someone to join an organization.",
    subject: "{{ inviter.name }} invited you to {{ team.name }}", preheader: "Join your team on {{ app.name }}.",
    variables: [...APP, { name: "inviter.name", sample: "Marcus Chen" }, { name: "team.name", sample: "Northwind Ops" }, { name: "role", sample: "Admin" }, { name: "invite_url", sample: "https://app.northwind.com/invite?t=…" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "Join ", v("team.name", "your team")),
      p(vb("inviter.name", "A teammate"), " invited you to join ", vb("team.name", "their team"), " on ", v("app.name"), " as ", v("role", "a member"), "."),
      button("Accept invitation", "{{ invite_url }}"),
      p(small("The invitation expires in 7 days. If you weren't expecting it, you can ignore this email.")),
      ...footer(),
    ),
  },
  {
    key: "security-alert", name: "Security alert", category: "security", description: "Tell someone about a new sign-in.",
    subject: "New sign-in to your {{ app.name }} account", preheader: "Was this you? Check the details.",
    variables: [...APP, { name: "user.first_name", sample: "Ada" }, { name: "event.device", sample: "Chrome on Windows" }, { name: "event.location", sample: "Nairobi, Kenya" }, { name: "event.time", sample: "22 Oct 2026, 09:41 WAT" }, { name: "event.unrecognized", description: "Whether the device is new", sample: "true" }, { name: "security_url", sample: "https://app.northwind.com/security" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "New sign-in to your account"),
      p("Hi ", v("user.first_name", "there"), ", your account was just used to sign in:"),
      section({}, p(bold("Device: "), v("event.device")), p(bold("Location: "), v("event.location")), p(bold("Time: "), v("event.time"))),
      showIf("event.unrecognized", section({ background: "#fef2f2", borderColor: "#fecaca" }, p(bold("We don't recognize this device."), " If it wasn't you, secure your account now."))),
      button("Secure your account", "{{ security_url }}"),
      p(small("If this was you, there's nothing to do.")),
      ...footer(),
    ),
  },
  {
    key: "payment-receipt", name: "Payment receipt", category: "billing", description: "Confirm a payment with its line items.",
    subject: "Receipt for your {{ app.name }} payment", preheader: "Thanks! Here's your receipt for {{ payment.total }}.",
    variables: [...APP, { name: "payment.number", sample: "R-10482" }, { name: "payment.date", sample: "22 October 2026" }, { name: "payment.method", sample: "Visa ending 4242" }, { name: "payment.items", description: "Lines on the receipt", sample: '[{"name":"Pro plan (monthly)","amount":"$49.00"},{"name":"Extra seats ×2","amount":"$20.00"}]' }, { name: "payment.total", sample: "$69.00" }, { name: "receipt_url", sample: "https://app.northwind.com/billing" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "Thanks for your payment"),
      p("We received ", vb("payment.total"), " on ", v("payment.date"), ". Here are the details:"),
      section({ background: "#ffffff", borderColor: "#e7e7ea" },
        columns([p(small("Receipt"))], [pa("right", v("payment.number"))]),
        hr({ spacing: 8 }),
        repeat("payment.items", "line", columns([p(v("line.name"))], [pa("right", v("line.amount"))])),
        hr({ spacing: 8 }),
        columns([p(bold("Total"))], [pa("right", vb("payment.total"))]),
        columns([p(small("Paid with"))], [pa("right", vs("payment.method"))]),
      ),
      button("View billing", "{{ receipt_url }}"),
      ...footer(),
    ),
  },
  {
    key: "invoice", name: "Invoice", category: "billing", description: "An invoice that is due, with a pay button.",
    subject: "Invoice {{ invoice.number }} from {{ app.name }}", preheader: "{{ invoice.total }} due on {{ invoice.due_date }}.",
    variables: [...APP, { name: "customer.name", sample: "Kwame Mensah" }, { name: "invoice.number", sample: "INV-2026-0142" }, { name: "invoice.due_date", sample: "5 November 2026" }, { name: "invoice.items", sample: '[{"name":"Consulting, October","amount":"$1,200.00"},{"name":"Hosting","amount":"$80.00"}]' }, { name: "invoice.total", sample: "$1,280.00" }, { name: "invoice.overdue", sample: "false" }, { name: "invoice_url", sample: "https://pay.northwind.com/inv/0142" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "Invoice ", v("invoice.number")),
      p("Hi ", v("customer.name", "there"), ", here is your invoice. It's due on ", vb("invoice.due_date"), "."),
      showIf("invoice.overdue", section({ background: "#fef2f2", borderColor: "#fecaca" }, p(bold("This invoice is overdue."), " Please pay it as soon as you can."))),
      section({ background: "#ffffff", borderColor: "#e7e7ea" },
        repeat("invoice.items", "line", columns([p(v("line.name"))], [pa("right", v("line.amount"))])),
        hr({ spacing: 8 }),
        columns([p(bold("Amount due"))], [pa("right", vb("invoice.total"))]),
      ),
      button("Pay invoice", "{{ invoice_url }}"),
      ...footer(),
    ),
  },
  {
    key: "subscription-update", name: "Subscription update", category: "billing", description: "Confirm a plan change.",
    subject: "You're now on {{ subscription.plan }}", preheader: "Your plan changed. Here's what's new.",
    variables: [...APP, { name: "user.first_name", sample: "Ada" }, { name: "subscription.old_plan", sample: "Starter" }, { name: "subscription.plan", sample: "Pro" }, { name: "subscription.price", sample: "$49/month" }, { name: "subscription.next_billing", sample: "22 November 2026" }, { name: "billing_url", sample: "https://app.northwind.com/billing" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "Your plan has changed"),
      p("Hi ", v("user.first_name", "there"), ", you've moved from ", v("subscription.old_plan"), " to ", vb("subscription.plan"), "."),
      section({}, columns([p(small("New plan")), p(vb("subscription.plan"))], [p(small("Price")), p(vb("subscription.price"))], [p(small("Next bill")), p(vb("subscription.next_billing"))])),
      button("Manage subscription", "{{ billing_url }}"),
      ...footer(),
    ),
  },
  {
    key: "trial-reminder", name: "Trial reminder", category: "lifecycle", description: "Nudge before a trial ends.",
    subject: "Your {{ app.name }} trial ends in {{ trial.days_left }} days", preheader: "Keep everything you've built by choosing a plan.",
    variables: [...APP, { name: "user.first_name", sample: "Ada" }, { name: "trial.days_left", sample: "3" }, { name: "trial.ends_on", sample: "25 October 2026" }, { name: "upgrade_url", sample: "https://app.northwind.com/upgrade" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "Your trial ends in ", v("trial.days_left"), " days"),
      p("Hi ", v("user.first_name", "there"), ", your free trial ends on ", vb("trial.ends_on"), ". Choose a plan to keep your projects, data and team."),
      showIf("trial.days_left|int <= 1", section({ background: "#fff8f0", borderColor: "#fde3c8" }, p(bold("Last day!"), " After today, your projects pause until you choose a plan."))),
      list("Everything you've built stays exactly as it is", "Plans start at $19 a month", "Cancel any time"),
      button("Choose a plan", "{{ upgrade_url }}"),
      ...footer(),
    ),
  },
  {
    key: "order-confirmation", name: "Order confirmation", category: "commerce", description: "Confirm an order with items and delivery details.",
    subject: "Order {{ order.number }} confirmed", preheader: "Thanks for your order! We're getting it ready.",
    variables: [...APP, { name: "customer.first_name", sample: "Kwame" }, { name: "order.number", sample: "#1042" }, { name: "order.items", sample: '[{"name":"Stoneware mug","quantity":2,"price":"$48.00"},{"name":"Linen tote","quantity":1,"price":"$41.99"}]' }, { name: "order.total", sample: "$89.99" }, { name: "order.address", sample: "14 Admiralty Way, Lekki, Lagos" }, { name: "order_url", sample: "https://shop.northwind.com/orders/1042" }],
    doc: doc(
      brand(), spacer(8),
      h(1, "Thanks for your order, ", v("customer.first_name", "friend"), "!"),
      p("We've received order ", vb("order.number"), " and we're getting it ready."),
      section({ background: "#ffffff", borderColor: "#e7e7ea" },
        repeat("order.items", "item", columns([p(v("item.name"), small(" × "), v("item.quantity"))], [pa("right", v("item.price"))])),
        hr({ spacing: 8 }),
        columns([p(bold("Total"))], [pa("right", vb("order.total"))]),
      ),
      h(3, "Delivering to"),
      p(v("order.address")),
      button("Track your order", "{{ order_url }}"),
      ...footer(),
    ),
  },
  {
    key: "notification", name: "Notification", category: "product", description: "A simple message about something that happened.",
    subject: "{{ notification.title }}", preheader: "{{ notification.summary }}",
    variables: [...APP, { name: "notification.title", sample: "Your export is ready" }, { name: "notification.summary", sample: "The CSV of October's orders finished processing." }, { name: "notification.body", sample: "Your export of 1,284 orders finished processing. It's available for 7 days." }, { name: "notification.action", sample: "Download export" }, { name: "notification.url", sample: "https://app.northwind.com/exports/42" }, { name: "settings_url", description: "Where the reader manages notifications", sample: "https://app.northwind.com/settings/notifications" }],
    doc: doc(
      brand(), spacer(8),
      h(2, v("notification.title")),
      p(v("notification.body")),
      { type: "button", attrs: { text: "Open", href: "{{ notification.url }}", variable: "notification.action" } },
      ...footer(p(small("You get these emails because notifications are on. "), link("Manage notifications", "{{ settings_url }}"))),
    ),
  },
  {
    key: "announcement", name: "Announcement", category: "marketing", description: "Share news, with highlights side by side.",
    subject: "Introducing {{ feature.name }}", preheader: "{{ feature.tagline }}",
    variables: [...APP, { name: "feature.name", sample: "Realtime dashboards" }, { name: "feature.tagline", sample: "Watch your numbers move as they happen." }, { name: "feature.url", sample: "https://northwind.com/changelog" }, { name: "unsubscribe_url", description: "Required for marketing email", sample: "https://northwind.com/unsubscribe?u=…" }],
    doc: doc(
      brand(), spacer(4),
      section({ background: "#1f1b2e", padding: 36, radius: 14, align: "center" },
        pa("center", t("NEW", [{ type: "textStyle", attrs: { color: "#ff7049", fontSize: "12px" } }, { type: "bold" }])),
        { type: "heading", attrs: { level: 1, textAlign: "center" }, content: [t("Introducing ", [{ type: "textStyle", attrs: { color: "#ffffff" } }]), { type: "variable", attrs: { name: "feature.name", fallback: "" }, marks: [{ type: "textStyle", attrs: { color: "#ffffff" } }] }] },
        pa("center", { type: "variable", attrs: { name: "feature.tagline", fallback: "" }, marks: [{ type: "textStyle", attrs: { color: "#cfc4fa" } }] }),
      ),
      spacer(8),
      columns(
        [h(3, "Live by default"), p(small("Charts update the moment your data changes."))],
        [h(3, "Share a link"), p(small("Send a read-only view to anyone, no account needed."))],
        [h(3, "Built in"), p(small("Every plan gets it today, at no extra cost."))],
      ),
      button("See what's new", "{{ feature.url }}", { align: "center" }),
      ...footer(p(small("You're receiving product news from "), v("app.name"), small(". "), link("Unsubscribe", "{{ unsubscribe_url }}"))),
    ),
  },
];

export const CATEGORIES = [
  ["onboarding", "Onboarding"], ["authentication", "Authentication"], ["security", "Security"], ["teams", "Teams"],
  ["billing", "Billing"], ["lifecycle", "Lifecycle"], ["commerce", "Commerce"], ["product", "Product"], ["marketing", "Marketing"], ["other", "Other"],
];

export const BLANK = {
  key: "blank", name: "Blank", category: "other", description: "Start from an empty email.", subject: "", preheader: "", variables: [...APP],
  doc: doc(brand(), spacer(8), h(1, "Your headline"), p("Write your message here, or type / to add a block."), ...footer()),
};
