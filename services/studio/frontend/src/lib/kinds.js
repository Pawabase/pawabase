// What Studio knows about each definition kind: how rows are keyed, what a
// new one starts as, and which fields are shown in the list.
const field = (name, type, extra = {}) => ({ name, type, ...extra });

export const KINDS = {
  schemas: {
    title: "Schemas",
    description: "Reusable field sets. Resources, routes and flows reference them; they compile to Pydantic models and OpenAPI.",
    columns: ["name", "description"],
    template: { name: "Address", description: "", fields: [field("street", "string", { required: true }), field("city", "string", { required: true }), field("postcode", "string")] },
  },
  transformers: {
    title: "Transformers",
    description: "Reshape what consumers see: omit, pick, set, rename, case.",
    columns: ["name", "description"],
    template: { name: "public_user", description: "", definition: { omit: ["password_hash"], rename: { created_at: "joined" }, case: "camel" } },
  },
  policies: {
    title: "Policies",
    description: "Named conditions. Reference them anywhere a policy is accepted; equality checks are pushed down to SQL.",
    columns: ["name", "description"],
    template: { name: "own_rows", description: "Signed-in users see their own rows", condition: { all: [{ authenticated: true }, { owner: "user_id" }] } },
  },
  resources: {
    title: "Resources",
    description: "Tables exposed as REST at /rest/v1/<name>, guarded per operation by policies.",
    columns: ["name", "table", "description"],
    template: {
      name: "todos",
      description: "",
      id_type: "integer",
      fields: [field("title", "string", { required: true, max_length: 200 }), field("done", "boolean", { default: false }), field("user_id", "string")],
      operations: {
        list: { enabled: true, policy: "own_rows" },
        get: { enabled: true, policy: "own_rows" },
        create: { enabled: true, policy: "authenticated" },
        update: { enabled: true, policy: "own_rows" },
        delete: { enabled: true, policy: "own_rows" },
      },
      relations: [],
      cache_ttl: 0,
    },
  },
  routes: {
    title: "Routes",
    description: "Custom endpoints under /rest/v1, handled by a flow or a Python function.",
    key: "id",
    columns: ["method", "path", "handler_type", "handler"],
    template: { method: "POST", path: "/checkout", name: "checkout", description: "", policy: "authenticated", input_fields: [field("cart_id", "integer", { required: true })], handler_type: "flow", handler: "checkout", rate_limit: { limit: 30, window: 60 } },
  },
  "mail-templates": {
    title: "Mail templates",
    description: "Subjects and bodies with {{ template }} values, sent by flows, functions and Akountz.",
    columns: ["name", "subject"],
    template: { name: "welcome", description: "", subject: "Welcome, {{ name }}", html: "<p>Hi {{ name }}, welcome aboard.</p>", text: "Hi {{ name }}, welcome aboard." },
  },
  subscriptions: {
    title: "Event subscriptions",
    description: "Run a flow or function, or broadcast to a realtime channel, when an event is emitted.",
    columns: ["name", "event", "target_type", "target", "enabled"],
    template: { name: "on_signup", description: "", event: "user.created", target_type: "flow", target: "welcome", condition: null, enabled: true },
  },
  webhooks: {
    title: "Webhooks",
    description: "Deliver events to external URLs, signed, with retries.",
    columns: ["name", "url", "events", "enabled"],
    template: { name: "crm", description: "", url: "https://example.com/hooks/pawabase", events: ["todos.*", "user.created"], headers: {}, enabled: true, max_attempts: 5 },
  },
  "inbound-hooks": {
    title: "Inbound hooks",
    description: "Receive webhooks from other services at /hooks/v1/<project>/<env>/<slug>, verified, then emitted as events or run as flows.",
    key: "slug",
    columns: ["slug", "verification", "target_type", "target", "enabled"],
    template: { slug: "stripe", name: "Stripe", description: "", verification: "hmac-sha256", signature_header: "stripe-signature", target_type: "event", target: "stripe.event", enabled: true },
  },
  schedules: {
    title: "Schedules",
    description: "Run a flow or function, or emit an event, on a cron expression or an interval.",
    columns: ["name", "cron", "interval_seconds", "target_type", "target", "enabled"],
    template: { name: "nightly_cleanup", description: "", cron: "0 3 * * *", interval_seconds: null, target_type: "flow", target: "cleanup", payload: null, enabled: true },
  },
  buckets: {
    title: "Buckets",
    key: "name",
    columns: ["name", "public"],
    template: { name: "avatars", description: "", public: true, read_policy: null, write_policy: "authenticated", accepts: ["image/*"], max_bytes: 5242880, signed_uploads: true },
  },
  flows: {
    key: "name",
    template: { name: "new_flow", description: "", definition: { nodes: [], edges: [] }, enabled: true, timeout: 60, record_runs: true },
  },
};

const SERVER_FIELDS = ["id", "environment", "environment_id", "created_at", "updated_at", "version", "secret_ciphertext", "has_secret"];

/** The editable body of a stored definition. */
export function editable(item) {
  const body = { ...item };
  for (const f of SERVER_FIELDS) delete body[f];
  return body;
}

export function keyOf(kind, item) {
  return item[KINDS[kind]?.key || "name"];
}
