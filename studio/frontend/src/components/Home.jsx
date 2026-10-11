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

function copy(text) {
  try { navigator.clipboard?.writeText(text); } catch { /* the text is visible to select */ }
}

/** A plain-language picture of how the parts connect: requests in, services in the middle, events out, monitoring under it all. */
function Diagram({ hrefFor }) {
  const go = (page) => router.visit(hrefFor(page));
  const node = (x, y, w, label, page, icon) => (
    <g key={label} className="dg-node" transform={`translate(${x} ${y})`} onClick={() => go(page)} role="link" tabIndex={0} onKeyDown={(event) => event.key === "Enter" && go(page)}>
      <rect width={w} height="46" rx="9" />
      <text x={w / 2} y="28" textAnchor="middle">{label}</text>
    </g>
  );
  return (
    <svg viewBox="0 0 830 330" className="diagram" role="img" aria-label="How Pawabase services connect">
      <text className="dg-cap" x="0" y="14">APPS</text>
      <text className="dg-cap" x="170" y="14">API</text>
      <text className="dg-cap" x="340" y="14">SERVICES</text>
      <text className="dg-cap" x="690" y="14">REACT</text>
      {node(0, 30, 140, "Web, mobile, servers", "keys")}
      {node(170, 30, 140, "Gateway · keys · limits", "keys")}
      {node(340, 30, 150, "Data", "resources")}
      {node(510, 30, 150, "Files", "storage")}
      {node(340, 94, 150, "Functions", "functions")}
      {node(510, 94, 150, "Users & access", "users")}
      <path className="dg-edge" d="M140 53h30M310 53h30M310 53c14 0 14 64 30 64" />
      <rect className="dg-bus" x="340" y="168" width="320" height="34" rx="9" />
      <text x="500" y="190" textAnchor="middle" className="dg-bus-label">Events · every change, one stream</text>
      <path className="dg-edge" d="M415 76v92M585 76v92M415 140v28M585 140v28" />
      {node(690, 30, 140, "Flows", "flows")}
      {node(690, 94, 140, "Queue & jobs", "jobs")}
      {node(690, 158, 140, "Realtime", "realtime")}
      {node(690, 222, 140, "Mail & webhooks", "mail")}
      <path className="dg-edge" d="M660 185h30M660 185c15 0 15-132 30-132M660 185c15 0 15-68 30-68M660 185c15 0 15 60 30 60" />
      <rect className="dg-bus" x="0" y="286" width="830" height="34" rx="9" />
      <text x="415" y="308" textAnchor="middle" className="dg-bus-label">Monitoring · logs, traces, alarms, usage, backups</text>
    </svg>
  );
}

/** The home of an environment: where things stand, what to do next, how it fits together, and every service. */
export default function Home({ env, hrefFor }) {
  const [dismissed, setDismissed] = useState(() => { try { return localStorage.getItem(DISMISS) === "1"; } catch { return false; } });
  const analytics = useApi(envPath(env, "/analytics"), { params: { range: "24h" } });
  const resources = useApi(envPath(env, "/resources"));
  const flows = useApi(envPath(env, "/flows"));
  const functions = useApi(envPath(env, "/functions"));
  const keys = useApi(envPath(env, "/keys"));
  const schema = useApi(envPath(env, "/schema"));
  const audit = useApi("/audit", { params: { limit: 40 } });

  const summary = analytics.data?.summary || {};
  const n = {
    collections: rows(resources)?.length ?? null,
    flows: rows(flows)?.length ?? null,
    functions: rows(functions)?.length ?? null,
    keys: rows(keys)?.length ?? null,
  };
  const applied = (schema.data?.resources || []).some((item) => item.state === "in_sync" && item.exists);
  const steps = [
    { label: "Create a collection", hint: "Describe your data once; get an API for it.", done: (n.collections || 0) > 0, page: "resources" },
    { label: "Apply it to the database", hint: "Review the SQL, then create the table.", done: applied, page: "resources" },
    { label: "Create an API key", hint: "A publishable key for apps, a secret key for servers.", done: (n.keys || 0) > 0, page: "keys" },
    { label: "Make a request", hint: "Try http get /health in the terminal, or use the API Explorer.", done: (summary.requests || 0) > 0, page: "explorer" },
    { label: "Build a flow", hint: "Automate something when a record changes.", done: (n.flows || 0) > 0, page: "flows" },
    { label: "Deploy a function", hint: "Write Python that runs next to your data.", done: (n.functions || 0) > 0, page: "functions" },
  ];
  const finished = steps.filter((step) => step.done).length;
  const next = steps.find((step) => !step.done);
  const events = (rows(audit) || audit.data?.entries || []).filter((entry) => !entry.env || entry.env === env).slice(0, 7);
  const recent = readRecent().map((key) => SERVICES.find((service) => service.key === key)).filter(Boolean);

  const meta = {
    data: n.collections != null ? plural(n.collections, "collection") : null,
    functions: n.functions != null ? plural(n.functions, "function") : null,
    automation: n.flows != null ? plural(n.flows, "flow") : null,
    secrets: n.keys != null ? plural(n.keys, "key") : null,
  };
  const kpis = [
    ["Requests", summary.requests != null ? Number(summary.requests).toLocaleString() : "—", "last 24 hours"],
    ["Errors", summary.error_rate != null ? `${(Number(summary.error_rate) * (Number(summary.error_rate) <= 1 ? 100 : 1)).toFixed(1)}%` : "—", "server errors"],
    ["Latency", summary.avg_latency_ms != null ? `${Math.round(summary.avg_latency_ms)} ms` : "—", "average"],
    ["Flow runs", summary.flow_runs != null ? Number(summary.flow_runs).toLocaleString() : "—", summary.flow_success_rate != null ? `${Math.round(Number(summary.flow_success_rate) * (Number(summary.flow_success_rate) <= 1 ? 100 : 1))}% succeed` : "last 24 hours"],
    ["Collections", n.collections != null ? String(n.collections) : "—", "defined"],
  ];

  return (
    <section className="home" aria-label="Home">
      <header className="home-hero">
        <div>
          <span className="eyebrow">Environment</span>
          <h1>{env}</h1>
          <p>Everything this backend does, as independent services that share one account, one set of access rules and one stream of events.</p>
        </div>
        <div className="home-hero-actions">
          <button type="button" className="hero-link" onClick={openTerminal}><Hair name="terminal" size={16} /> Terminal <kbd>⌘J</kbd></button>
          <button type="button" className="hero-link" onClick={() => startTour()}><Hair name="events" size={16} /> Take the tour</button>
        </div>
      </header>

      <div className="kpis">
        {kpis.map(([label, value, sub]) => (
          <div key={label} className="kpi-cell"><span>{label}</span><b>{value}</b><small>{sub}</small></div>
        ))}
      </div>

      <div className="home-cols">
        {!(dismissed && finished === steps.length) && (
          <section className="panel">
            <div className="panel-head">
              <h2>Get started</h2>
              <span className="progress-text">{finished} of {steps.length}</span>
            </div>
            <div className="progress"><i style={{ width: `${(finished / steps.length) * 100}%` }} /></div>
            <ol className="checklist">
              {steps.map((step) => (
                <li key={step.label} className={step.done ? "done" : step === next ? "next" : ""}>
                  <span className="check" aria-hidden>{step.done ? <Hair name="check" size={14} /> : null}</span>
                  <span className="grow">
                    <b>{step.label}</b>
                    {!step.done && <small>{step.hint}</small>}
                  </span>
                  {!step.done && <Link href={hrefFor(step.page)} className={step === next ? "go primary" : "go"}>{step === next ? "Start" : "Open"}</Link>}
                </li>
              ))}
            </ol>
            {finished === steps.length && <button type="button" className="panel-foot" onClick={() => { setDismissed(true); try { localStorage.setItem(DISMISS, "1"); } catch { /* shown again next time */ } }}>All done. Hide this</button>}
          </section>
        )}
        <section className="panel">
          <div className="panel-head">
            <h2>Recent activity</h2>
            <Link href="/audit" className="panel-link">Audit log</Link>
          </div>
          {events.length === 0 ? (
            <p className="panel-empty">Changes made in Studio, the API and the CLI appear here, with who made them.</p>
          ) : (
            <ul className="feed">
              {events.map((entry, index) => (
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

      <section className="panel wide">
        <div className="panel-head"><h2>How it fits together</h2><span className="panel-sub">Click a part to open it</span></div>
        <Diagram hrefFor={hrefFor} />
      </section>

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

      <div className="home-row-head"><h2>Try this</h2><span className="panel-sub">Paste into the terminal (⌘J)</span></div>
      <div className="try-grid">
        {[
          ["Check the API is up", "http get /health", "Sends a request as an anonymous caller and shows the status, timing and body."],
          ["List a collection", "data list <collection> 10", "Reads the first rows of any collection, through the same rules as your app."],
          ["Emit an event", "events emit hello {} --confirm", "Publishes an event so you can watch what reacts to it in Events & runs."],
          ["Test a write", "http post /rest/v1/<collection> title=Hello --confirm", "Creates a record the way an app would, and prints the response."],
        ].map(([title, command, note]) => (
          <div key={title} className="try-card">
            <b>{title}</b>
            <code>{command}</code>
            <small>{note}</small>
            <div className="row"><button type="button" onClick={() => copy(command)}>Copy</button><button type="button" onClick={openTerminal}>Open terminal</button></div>
          </div>
        ))}
      </div>
    </section>
  );
}
