import { Link, router } from "@inertiajs/react";
import { useState } from "react";
import Hair from "./Hair";
import { CATEGORIES, SERVICES, readRecent } from "../lib/services";
import { startTour } from "../lib/tour";
import { envPath, useApi } from "../lib/api";
import { when } from "./ui";

const LABEL = Object.fromEntries(CATEGORIES.map((category) => [category.id, category.label]));
const DISMISS = "pawabase.start.dismissed";

const openTerminal = () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "j", ctrlKey: true, bubbles: true }));
const rows = (state) => state.data?.data ?? (Array.isArray(state.data) ? state.data : null);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const copy = (text) => { try { navigator.clipboard?.writeText(text); } catch { /* visible to select */ } };

// ── the picture: Pawabase as a stack you build on ─────────────────────────────────────────────────────────────────────
const U = 25;
const OX = 270;
const OY = 150;
const iso = (x, y, z) => [OX + (x - y) * U * 0.866, OY + (x + y) * U * 0.5 - z * U];
const pts = (list) => list.map(([x, y, z]) => iso(x, y, z).map((n) => n.toFixed(1)).join(",")).join(" ");

function Block({ x, y, z, w, d, h, label, page, tone = "a", onGo, caption }) {
  const top = pts([[x, y, z + h], [x + w, y, z + h], [x + w, y + d, z + h], [x, y + d, z + h]]);
  const left = pts([[x, y + d, z + h], [x + w, y + d, z + h], [x + w, y + d, z], [x, y + d, z]]);
  const right = pts([[x + w, y, z + h], [x + w, y + d, z + h], [x + w, y + d, z], [x + w, y, z]]);
  const [cx, cy] = iso(x + w / 2, y + d / 2, z + h);
  return (
    <g className={`iso iso-${tone}`} onClick={() => page && onGo(page)} role={page ? "link" : undefined} tabIndex={page ? 0 : undefined} onKeyDown={(event) => event.key === "Enter" && page && onGo(page)}>
      <polygon className="iso-left" points={left} />
      <polygon className="iso-right" points={right} />
      <polygon className="iso-top" points={top} />
      {label && <text x={cx} y={cy + 4} textAnchor="middle" className="iso-label">{label}</text>}
      {caption && <title>{caption}</title>}
    </g>
  );
}

function Stack({ onGo }) {
  const [ex, ey] = iso(-0.4, 8.4, 5);
  return (
    <svg viewBox="0 0 540 400" className="stack-art" role="img" aria-label="Pawabase as layers: data at the bottom, services above it, events across them, automation on top">
      <ellipse cx={OX} cy={OY + 195} rx="235" ry="40" className="stack-shadow" />
      <Block x={0} y={0} z={0} w={8} d={8} h={0.7} tone="base" label="" page="resources" onGo={onGo} caption="Data: collections on real tables" />
      <Block x={0.5} y={2.8} z={0.7} w={2.2} d={2.4} h={1.4} label="Files" page="storage" onGo={onGo} caption="Files" />
      <Block x={2.9} y={2.8} z={0.7} w={2.2} d={2.4} h={1.4} label="Functions" page="functions" onGo={onGo} caption="Functions & API" />
      <Block x={5.3} y={2.8} z={0.7} w={2.2} d={2.4} h={1.4} label="Users" page="users" onGo={onGo} caption="Users & access" />
      <g className="iso-events" onClick={() => onGo("events")} role="link" tabIndex={0} onKeyDown={(event) => event.key === "Enter" && onGo("events")}>
        <polygon points={pts([[-0.4, -0.4, 5], [8.4, -0.4, 5], [8.4, 8.4, 5], [-0.4, 8.4, 5]])} />
        <text x={ex + 6} y={ey + 20} textAnchor="start">Events · one stream</text>
        <title>Events: every change is one stream</title>
      </g>
      <path className="stack-link" d={[0.5, 2.9, 5.3].map((x) => `M${iso(x + 1.1, 4, 2.1).join(",")} L${iso(x + 1.1, 4, 5).join(",")}`).join(" ")} />
      <Block x={0.5} y={2.8} z={5.05} w={2.2} d={2.4} h={1.2} label="Flows" page="flows" tone="hot" onGo={onGo} caption="Flows" />
      <Block x={2.9} y={2.8} z={5.05} w={2.2} d={2.4} h={1.2} label="Queue" page="jobs" tone="hot" onGo={onGo} caption="Queue & events" />
      <Block x={5.3} y={2.8} z={5.05} w={2.2} d={2.4} h={1.2} label="Realtime" page="realtime" tone="hot" onGo={onGo} caption="Realtime" />
    </svg>
  );
}

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

function MiniDocs({ hrefFor }) {
  const [active, setActive] = useState(DOCS[0].key);
  const doc = DOCS.find((item) => item.key === active);
  return (
    <section className="docs" aria-label="Learn">
      <aside className="docs-list" role="tablist" aria-label="Topics">
        <span className="docs-kicker">Learn the model</span>
        {DOCS.map((item) => (
          <button key={item.key} type="button" role="tab" aria-selected={active === item.key} className={active === item.key ? "on" : ""} onClick={() => setActive(item.key)}>{item.label}</button>
        ))}
      </aside>
      <article className="docs-body" role="tabpanel">
        <h3>{doc.title}</h3>
        <p>{doc.body}</p>
        <div className="docs-code">
          <pre><code>{doc.code}</code></pre>
          <button type="button" onClick={() => copy(doc.code)}>Copy</button>
        </div>
        <div className="docs-more">
          <span>Open in Studio</span>
          {doc.more.map(([label, page]) => <Link key={page} href={hrefFor(page)}>{label}</Link>)}
          <button type="button" className="docs-term" onClick={openTerminal}>Terminal <kbd>⌘J</kbd></button>
        </div>
      </article>
    </section>
  );
}

/** The home of an environment: a picture of the product, the next thing to do, a short course, and every service. */
export default function Home({ env, hrefFor }) {
  const [dismissed, setDismissed] = useState(() => { try { return localStorage.getItem(DISMISS) === "1"; } catch { return false; } });
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
    { label: "Create a collection", hint: "Describe your data once; get an API for it.", done: (n.collections || 0) > 0, page: "resources" },
    { label: "Apply it to the database", hint: "Review the SQL, then create the table.", done: applied, page: "resources" },
    { label: "Create an API key", hint: "A publishable key for apps, a secret key for servers.", done: (n.keys || 0) > 0, page: "keys" },
    { label: "Make a request", hint: "Try http get /health in the terminal.", done: (requests || 0) > 0, page: "explorer" },
    { label: "Build a flow", hint: "Automate something when a record changes.", done: (n.flows || 0) > 0, page: "flows" },
    { label: "Deploy a function", hint: "Write Python that runs next to your data.", done: (n.functions || 0) > 0, page: "functions" },
  ];
  const finished = steps.filter((step) => step.done).length;
  const next = steps.find((step) => !step.done);
  const events = (rows(audit) || audit.data?.entries || []).filter((entry) => !entry.env || entry.env === env);
  const last = events[0];
  const recent = readRecent().map((key) => SERVICES.find((service) => service.key === key)).filter(Boolean);
  const meta = {
    data: n.collections != null ? plural(n.collections, "collection") : null,
    functions: n.functions != null ? plural(n.functions, "function") : null,
    automation: n.flows != null ? plural(n.flows, "flow") : null,
    secrets: n.keys != null ? plural(n.keys, "key") : null,
  };
  const go = (page) => router.visit(hrefFor(page));
  const facts = [
    n.collections != null && plural(n.collections, "collection"),
    n.functions != null && plural(n.functions, "function"),
    n.flows != null && plural(n.flows, "flow"),
    last && `last change ${when(last.created_at)}`,
  ].filter(Boolean);

  return (
    <section className="home" aria-label="Home">
      <header className="stage">
        <div className="stage-copy">
          <span className="stage-env"><i />{env}</span>
          <h1>Bring your infrastructure.<br /><em>Build your backend.</em></h1>
          <p>Define your data once. Pawabase gives it an API, access rules, sign-in, files, realtime and background work, on the database and storage you already run.</p>
          <div className="stage-actions">
            <Link href={hrefFor("resources")} className="cta primary">{(n.collections || 0) > 0 ? "Open your collections" : "Create your first collection"}</Link>
            <button type="button" className="cta" onClick={openTerminal}><Hair name="terminal" size={16} /> Open the terminal</button>
            <button type="button" className="cta ghost" onClick={() => startTour()}>Take the tour</button>
          </div>
          {facts.length > 0 && <div className="stage-facts">{facts.map((fact) => <span key={fact}>{fact}</span>)}</div>}
        </div>
        <div className="stage-art"><Stack onGo={go} /></div>
      </header>

      <div className="home-cols">
        {!(dismissed && finished === steps.length) && (
          <section className="panel">
            <div className="panel-head"><h2>Get started</h2><span className="progress-text">{finished} of {steps.length}</span></div>
            <div className="progress"><i style={{ width: `${(finished / steps.length) * 100}%` }} /></div>
            <ol className="checklist">
              {steps.map((step) => (
                <li key={step.label} className={step.done ? "done" : step === next ? "next" : ""}>
                  <span className="check" aria-hidden>{step.done ? <Hair name="check" size={14} /> : null}</span>
                  <span className="grow"><b>{step.label}</b>{!step.done && <small>{step.hint}</small>}</span>
                  {!step.done && <Link href={hrefFor(step.page)} className={step === next ? "go primary" : "go"}>{step === next ? "Start" : "Open"}</Link>}
                </li>
              ))}
            </ol>
            {finished === steps.length && <button type="button" className="panel-foot" onClick={() => { setDismissed(true); try { localStorage.setItem(DISMISS, "1"); } catch { /* shown again */ } }}>All done. Hide this</button>}
          </section>
        )}
        <section className="panel">
          <div className="panel-head"><h2>Recent changes</h2><Link href="/audit" className="panel-link">Audit log</Link></div>
          {events.length === 0 ? (
            <p className="panel-empty">Nothing has changed yet. Changes made in Studio, the API and the CLI appear here, with who made them.</p>
          ) : (
            <ul className="feed">
              {events.slice(0, 6).map((entry, index) => (
                <li key={entry.id || index}>
                  <span className="feed-dot" />
                  <span className="grow"><b>{entry.action}</b>{entry.target ? <small> · {entry.target}</small> : null}</span>
                  <span className="feed-when">{when(entry.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <MiniDocs hrefFor={hrefFor} />

      <div className="home-row-head">
        <h2>Services</h2>
        {recent.length > 0 && (
          <div className="row wrap">
            <span className="quick-label">Recent</span>
            {recent.map((service) => <Link key={service.key} href={hrefFor(service.pages[0][0])} className="quick-action quiet"><Hair name={service.icon} size={16} />{service.label}</Link>)}
          </div>
        )}
      </div>
      <div className="catalog-grid">
        {SERVICES.map((service) => (
          <article key={service.key} className="service-cell">
            <span className="service-cell-eyebrow">{LABEL[service.category]}</span>
            <Link href={hrefFor(service.pages[0][0])} className="service-cell-head">
              <Hair name={service.icon} size={24} />
              <b>{service.label}</b>
              {meta[service.key] && <em className="count">{meta[service.key]}</em>}
            </Link>
            <p>{service.description}</p>
            <nav className="service-pages" aria-label={`${service.label} pages`}>
              {service.pages.map(([key, label]) => <Link key={key} href={hrefFor(key)}>{label}</Link>)}
            </nav>
          </article>
        ))}
      </div>
    </section>
  );
}
