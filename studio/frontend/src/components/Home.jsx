import { Link, usePage } from "@inertiajs/react";
import { useState } from "react";
import Hair from "./Hair";
import { SERVICES } from "../lib/services";
import { startTour } from "../lib/tour";
import { envPath, useApi } from "../lib/api";
import { when } from "./ui";

const DISMISS = "pawabase.start.dismissed";

const openTerminal = () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "j", ctrlKey: true, bubbles: true }));
const rows = (state) => state.data?.data ?? (Array.isArray(state.data) ? state.data : null);
const copy = (text) => { try { navigator.clipboard?.writeText(text); } catch { /* visible to select */ } };

// ── mini docs ─────────────────────────────────────────────────────────────────────────────────────────────────────────
const DOCS = [
  {
    key: "collections", label: "Collections", page: "resources", title: "A collection is a table with an API",
    body: "Describe the fields once. Pawabase makes the table, validates writes, and serves it at /rest/v1/<name> with filtering, sorting and relations. Changes you save are not applied to the database until you apply them, and every change can be rolled back.",
    code: 'GET /rest/v1/todos?filter[done]=eq.false&sort=-created_at\napikey: <your publishable key>',
    more: [["Data model", "resources"], ["SQL", "database"]],
  },
  {
    key: "access", label: "Access", page: "policies", title: "Who can do what is written next to the data",
    body: "Each operation on a collection names a policy: public, authenticated, owner, or your own condition. Policies are checked on every request, for every client, so there is nothing to forget in application code.",
    code: '"operations": {\n  "list":   { "enabled": true, "policy": "public" },\n  "create": { "enabled": true, "policy": "authenticated" },\n  "update": { "enabled": true, "policy": "owner" }\n}',
    more: [["Policies", "policies"], ["Users", "users"]],
  },
  {
    key: "auth", label: "Sign-in", page: "users", title: "Users, organizations and sessions are built in",
    body: "Email and password, two-step verification, organizations and invitations. Your app calls the auth service with the publishable key; Studio shows the people and lets you manage them.",
    code: 'POST /auth/v1/signup\napikey: <your publishable key>\n\n{ "email": "ada@example.com", "password": "Str0ng!pass" }',
    more: [["Users", "users"], ["API keys", "keys"]],
  },
  {
    key: "events", label: "Events", page: "events", title: "Everything that happens is an event",
    body: "Creating, updating or deleting a record publishes <collection>.created, .updated or .deleted. Flows, functions and webhooks subscribe to events, and you can emit your own. Signed inbound hooks turn another service's webhook into an event.",
    code: "events emit order.paid {\"id\": 42} --confirm",
    more: [["Event triggers", "subscriptions"], ["Flows", "flows"], ["Webhooks in", "inbound-hooks"]],
  },
  {
    key: "functions", label: "Functions", page: "functions", title: "Write Python that runs next to your data",
    body: "Functions run in processes of their own, with memory and time limits, so one bad function can't take the backend down. Deploy with the CLI and call them over HTTP, from a flow, or on a schedule.",
    code: 'from pawabase.functions import FunctionContext, function\n\n@function("hello", policy="public")\nasync def hello(ctx: FunctionContext):\n    return {"message": "hello"}',
    more: [["Functions", "functions"], ["Routes", "routes"]],
  },
  {
    key: "environments", label: "Environments", page: "settings", title: "Development, staging and production side by side",
    body: "Each environment has its own data, keys and settings. Use branches to try a change safely, then promote it as a release. The environment switcher is in the top bar; History holds branches and releases.",
    code: "http get /health          # the API of this environment\nhttp get /rest/v1/todos   # a collection, as an anonymous caller",
    more: [["Settings", "settings"], ["Backups", "backups"]],
  },
];

function DocsList({ hrefFor }) {
  const [open, setOpen] = useState(DOCS[0].key);
  return (
    <ul className="acc">
      {DOCS.map((doc) => (
        <li key={doc.key} className={open === doc.key ? "open" : ""}>
          <button type="button" className="acc-head" aria-expanded={open === doc.key} onClick={() => setOpen(open === doc.key ? "" : doc.key)}>
            <span>{doc.title}</span>
            <Hair name="chevron" size={15} />
          </button>
          {open === doc.key && (
            <div className="acc-body">
              <p>{doc.body}</p>
              <div className="docs-code">
                <pre><code>{doc.code}</code></pre>
                <button type="button" onClick={() => copy(doc.code)}>Copy</button>
              </div>
              <div className="docs-more">
                {doc.more.map(([label, page]) => <Link key={page} href={hrefFor(page)}>{label}</Link>)}
              </div>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/** The dashboard of an environment: what it is, what to set up, what changed, and every service, in plain cards. */
export default function Home({ env, hrefFor }) {
  const { gateway_url: gatewayUrl } = usePage().props;
  const [dismissed, setDismissed] = useState(() => { try { return localStorage.getItem(DISMISS) === "1"; } catch { return false; } });
  const [copied, setCopied] = useState(false);
  const analytics = useApi(envPath(env, "/analytics"), { params: { range: "24h" } });
  const resources = useApi(envPath(env, "/resources"));
  const flows = useApi(envPath(env, "/flows"));
  const functions = useApi(envPath(env, "/functions"));
  const keys = useApi(envPath(env, "/keys"));
  const schema = useApi(envPath(env, "/schema"));
  const audit = useApi("/audit", { params: { limit: 40 } });

  const requests = analytics.data?.summary?.requests;
  const n = { collections: rows(resources)?.length ?? null, flows: rows(flows)?.length ?? null, functions: rows(functions)?.length ?? null, keys: rows(keys)?.length ?? null };
  const applied = (schema.data?.resources || []).some((item) => item.state === "in_sync" && item.exists);
  const steps = [
    { label: "Create a collection", done: (n.collections || 0) > 0, page: "resources" },
    { label: "Apply it to the database", done: applied, page: "resources" },
    { label: "Create an API key", done: (n.keys || 0) > 0, page: "keys" },
    { label: "Make a request", done: (requests || 0) > 0, page: "explorer" },
    { label: "Build a flow", done: (n.flows || 0) > 0, page: "flows" },
    { label: "Deploy a function", done: (n.functions || 0) > 0, page: "functions" },
  ];
  const finished = steps.filter((step) => step.done).length;
  const events = (rows(audit) || audit.data?.entries || []).filter((entry) => !entry.env || entry.env === env);
  const counts = {
    data: n.collections, functions: n.functions, automation: n.flows, secrets: n.keys,
  };
  const unit = { data: "collection", functions: "function", automation: "flow", secrets: "key" };

  return (
    <section className="dash" aria-label="Dashboard">
      <header className="dash-title">
        <h1>{env}</h1>
        <span className="dash-env"><i />environment</span>
        <span className="grow" />
        <button type="button" className="text-btn" onClick={() => startTour()}><Hair name="events" size={16} /> Take the tour</button>
        <button type="button" className="text-btn" onClick={openTerminal}><Hair name="terminal" size={16} /> Terminal <kbd>⌘J</kbd></button>
      </header>

      <div className="dash-grid">
        <section className="panel span-4">
          <div className="panel-head"><h2>This environment</h2></div>
          <dl className="fact-list">
            <div><dt>API endpoint</dt><dd><code>{gatewayUrl || "—"}</code>{gatewayUrl && <button type="button" className="icon-text" onClick={() => { copy(gatewayUrl); setCopied(true); setTimeout(() => setCopied(false), 1400); }}>{copied ? "Copied" : "Copy"}</button>}</dd></div>
            <div><dt>Collections</dt><dd>{n.collections ?? "—"}</dd></div>
            <div><dt>Functions</dt><dd>{n.functions ?? "—"}</dd></div>
            <div><dt>Flows</dt><dd>{n.flows ?? "—"}</dd></div>
            <div><dt>API keys</dt><dd>{n.keys ?? "—"}</dd></div>
            <div><dt>Requests, 24h</dt><dd>{requests != null ? Number(requests).toLocaleString() : "—"}</dd></div>
          </dl>
        </section>

        {!(dismissed && finished === steps.length) && (
          <section className="panel span-4">
            <div className="panel-head"><h2>Set up</h2><span className="progress-text">{finished} of {steps.length}</span></div>
            <ol className="todo">
              {steps.map((step) => (
                <li key={step.label} className={step.done ? "done" : ""}>
                  <Link href={hrefFor(step.page)}>
                    <span className="check" aria-hidden>{step.done ? <Hair name="check" size={13} /> : null}</span>
                    <span>{step.label}</span>
                    {!step.done && <Hair name="chevron" size={14} className="go-icon" />}
                  </Link>
                </li>
              ))}
            </ol>
            {finished === steps.length && <button type="button" className="panel-foot" onClick={() => { setDismissed(true); try { localStorage.setItem(DISMISS, "1"); } catch { /* shown again */ } }}>Hide this</button>}
          </section>
        )}

        <section className="panel span-4">
          <div className="panel-head"><h2>Recent changes</h2><Link href="/audit" className="panel-link">All</Link></div>
          {events.length === 0 ? (
            <p className="panel-empty">Nothing yet. Changes made in Studio, the API and the CLI appear here.</p>
          ) : (
            <ul className="feed">
              {events.slice(0, 6).map((entry, index) => (
                <li key={entry.id || index}>
                  <span className="grow"><b>{entry.action}</b>{entry.target ? <small> {entry.target}</small> : null}</span>
                  <span className="feed-when">{when(entry.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel span-8">
          <div className="panel-head"><h2>Services</h2><span className="panel-sub">{SERVICES.length}</span></div>
          <ul className="service-list">
            {SERVICES.map((service) => (
              <li key={service.key}>
                <Link href={hrefFor(service.pages[0][0])}>
                  <Hair name={service.icon} size={22} />
                  <span className="grow">
                    <b>{service.label}</b>
                    <small>{service.pages.map(([, label]) => label).join(" · ")}</small>
                  </span>
                  {counts[service.key] != null && <span className="count">{counts[service.key]} {unit[service.key]}{counts[service.key] === 1 ? "" : "s"}</span>}
                  <Hair name="chevron" size={14} className="go-icon" />
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <section className="panel span-4">
          <div className="panel-head"><h2>Learn</h2><span className="panel-sub">Two minutes each</span></div>
          <DocsList hrefFor={hrefFor} />
        </section>
      </div>
    </section>
  );
}
