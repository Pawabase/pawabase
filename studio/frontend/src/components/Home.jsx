import { Link, usePage } from "@inertiajs/react";
import { useState } from "react";
import Art from "./Art";
import Hair from "./Hair";
import { Sheet } from "./ui";
import { SERVICES } from "../lib/services";
import { startTour } from "../lib/tour";
import { envPath, useApi } from "../lib/api";
import { when } from "./ui";

const DISMISS = "pawabase.start.dismissed";
const openTerminal = () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "j", ctrlKey: true, bubbles: true }));
const rows = (state) => state.data?.data ?? (Array.isArray(state.data) ? state.data : null);
const copy = (text) => { try { navigator.clipboard?.writeText(text); } catch { /* visible to select */ } };
const service = (key) => SERVICES.find((item) => item.key === key);

// The map. Coordinates are in a 1000 x 400 space: edges are drawn from them, nodes are placed at the same points in percent.
const NODES = [
  { id: "apps", x: 70, y: 215, label: "Your apps", note: "web · mobile · servers", glyph: "services" },
  { id: "functions", x: 280, y: 105, service: "functions" },
  { id: "access", x: 280, y: 215, service: "access" },
  { id: "secrets", x: 280, y: 325, service: "secrets" },
  { id: "data", x: 520, y: 125, service: "data" },
  { id: "files", x: 520, y: 315, service: "files" },
  { id: "events", x: 720, y: 215, service: "events", spine: true },
  { id: "automation", x: 925, y: 105, service: "automation" },
  { id: "realtime", x: 925, y: 215, service: "realtime" },
  { id: "mail", x: 925, y: 325, service: "mail" },
];
const EDGES = [
  ["apps", "functions"], ["apps", "access"], ["apps", "secrets"],
  ["functions", "data"], ["access", "data"], ["secrets", "files"],
  ["data", "events"], ["files", "events"],
  ["events", "automation"], ["events", "realtime"], ["events", "mail"],
];
const at = (id) => NODES.find((node) => node.id === id);
const curve = ([from, to]) => {
  const a = at(from), b = at(to);
  const mid = (a.x + b.x) / 2;
  return `M${a.x} ${a.y} C${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}`;
};

function Map({ hrefFor, counts }) {
  const [hot, setHot] = useState(null);
  const lit = (edge) => hot && (edge[0] === hot || edge[1] === hot);
  return (
    <div className="map" onMouseLeave={() => setHot(null)}>
      <svg className="map-edges" viewBox="0 0 1000 400" preserveAspectRatio="none" aria-hidden>
        <path className="map-spine" d="M720 50V380" />
        {EDGES.map((edge) => <path key={edge.join("-")} d={curve(edge)} className={lit(edge) ? "on" : ""} />)}
        <text x="70" y="22" className="map-cap">APPS</text>
        <text x="280" y="22" className="map-cap">FRONT DOOR</text>
        <text x="520" y="22" className="map-cap">DATA</text>
        <text x="720" y="22" className="map-cap">EVENTS</text>
        <text x="925" y="22" className="map-cap">REACT</text>
      </svg>
      {NODES.map((node) => {
        const item = node.service ? service(node.service) : null;
        const count = item ? counts[item.key] : null;
        const body = (
          <>
            <span className="map-glyph"><Hair name={item ? item.icon : node.glyph} size={24} /></span>
            <b>{item ? item.label : node.label}</b>
            <small>{item ? item.pages.map(([, label]) => label).slice(0, 2).join(" · ") : node.note}</small>
            {count != null && <em>{count}</em>}
          </>
        );
        const style = { left: `${node.x / 10}%`, top: `${(node.y / 400) * 100}%` };
        return item ? (
          <Link key={node.id} href={hrefFor(item.pages[0][0])} className={`map-node ${hot === node.id ? "hot" : ""}`} style={style} onMouseEnter={() => setHot(node.id)} onFocus={() => setHot(node.id)}>{body}</Link>
        ) : (
          <div key={node.id} className="map-node static" style={style}>{body}</div>
        );
      })}
    </div>
  );
}

const TOPICS = [
  { key: "data", title: "Collections", line: "A table with an API.", body: "Describe the fields once. Pawabase makes the table, validates writes, and serves it at /rest/v1/<name> with filtering, sorting and relations. Changes you save are not applied to the database until you apply them, and every change can be rolled back.", code: 'GET /rest/v1/todos?filter[done]=eq.false&sort=-created_at\napikey: <your publishable key>', more: [["Data model", "resources"], ["SQL", "database"]] },
  { key: "access", title: "Access", line: "Rules next to the data.", body: "Each operation on a collection names a policy: public, authenticated, owner, or your own condition. Policies are checked on every request, for every client, so there is nothing to forget in application code.", code: '"operations": {\n  "list":   { "enabled": true, "policy": "public" },\n  "create": { "enabled": true, "policy": "authenticated" },\n  "update": { "enabled": true, "policy": "owner" }\n}', more: [["Policies", "policies"], ["Users", "users"]] },
  { key: "events", title: "Events", line: "Everything that happens.", body: "Creating, updating or deleting a record publishes <collection>.created, .updated or .deleted. Flows, functions and webhooks subscribe to events, and you can emit your own. Signed inbound hooks turn another service's webhook into an event.", code: "events emit order.paid {\"id\": 42} --confirm", more: [["Event triggers", "subscriptions"], ["Flows", "flows"], ["Webhooks in", "inbound-hooks"]] },
  { key: "functions", title: "Functions", line: "Python next to your data.", body: "Functions run in processes of their own, with memory and time limits, so one bad function can't take the backend down. Deploy with the CLI and call them over HTTP, from a flow, or on a schedule.", code: 'from pawabase.functions import FunctionContext, function\n\n@function("hello", policy="public")\nasync def hello(ctx: FunctionContext):\n    return {"message": "hello"}', more: [["Functions", "functions"], ["Routes", "routes"]] },
  { key: "secrets", title: "Keys", line: "One for apps, one for servers.", body: "A publishable key identifies your app and is safe in a browser. A secret key is for servers and bypasses policies, so it never leaves them. Keys belong to an environment.", code: 'curl http://localhost:8080/rest/v1/todos \\\n  -H "apikey: <your publishable key>"', more: [["API keys", "keys"], ["Secrets", "secrets"]] },
  { key: "monitoring", title: "Environments", line: "Dev, staging, production.", body: "Each environment has its own data, keys and settings. Use branches to try a change safely, then promote it as a release. Switch environment from the top bar; History holds branches and releases.", code: "http get /health          # this environment's API\nhttp get /rest/v1/todos   # a collection, as an anonymous caller", more: [["Settings", "settings"], ["Backups", "backups"]] },
];

/** The dashboard of an environment: a map of its parts, what to set up, what changed, and a short course. */
export default function Home({ env, hrefFor }) {
  const { gateway_url: gatewayUrl } = usePage().props;
  const [dismissed, setDismissed] = useState(() => { try { return localStorage.getItem(DISMISS) === "1"; } catch { return false; } });
  const [copied, setCopied] = useState(false);
  const [topic, setTopic] = useState(null);
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
    data: n.collections != null ? `${n.collections}` : null,
    functions: n.functions != null ? `${n.functions}` : null,
    automation: n.flows != null ? `${n.flows}` : null,
    secrets: n.keys != null ? `${n.keys}` : null,
  };
  const open = TOPICS.find((item) => item.key === topic);

  return (
    <section className="home2" aria-label="Environment">
      <header className="home2-head">
        <div>
          <span className="home2-kicker">Environment</span>
          <h1>{env}</h1>
        </div>
        <div className="home2-endpoint">
          <span>API endpoint</span>
          <code>{gatewayUrl || "—"}</code>
          {gatewayUrl && <button type="button" onClick={() => { copy(gatewayUrl); setCopied(true); setTimeout(() => setCopied(false), 1400); }}>{copied ? "Copied" : "Copy"}</button>}
        </div>
        <div className="home2-tools">
          <button type="button" onClick={() => startTour()}>Tour</button>
          <button type="button" onClick={openTerminal}>Terminal <kbd>⌘J</kbd></button>
        </div>
      </header>

      <Map hrefFor={hrefFor} counts={counts} />

      <div className="operate">
        <span className="operate-label">Operate</span>
        <span className="operate-line" aria-hidden />
        {["monitoring", "settings"].map((key) => {
          const item = service(key);
          return (
            <Link key={key} href={hrefFor(item.pages[0][0])} className="operate-item">
              <Hair name={item.icon} size={20} />
              <span><b>{item.label}</b><small>{item.pages.map(([, label]) => label).join(" · ")}</small></span>
            </Link>
          );
        })}
      </div>

      <div className="home2-cols">
        <section>
          <h2>Set up <span>{finished} of {steps.length}</span></h2>
          {!(dismissed && finished === steps.length) ? (
            <ol className="steps">
              {steps.map((step, index) => (
                <li key={step.label} className={step.done ? "done" : ""}>
                  <Link href={hrefFor(step.page)}>
                    <span className="steps-n">{step.done ? <Hair name="check" size={13} /> : index + 1}</span>
                    <span>{step.label}</span>
                  </Link>
                </li>
              ))}
            </ol>
          ) : <p className="muted">Everything is set up.</p>}
          {finished === steps.length && !dismissed && <button type="button" className="quiet" onClick={() => { setDismissed(true); try { localStorage.setItem(DISMISS, "1"); } catch { /* shown again */ } }}>Hide this</button>}
        </section>
        <section>
          <h2>Recent changes <Link href="/audit">All</Link></h2>
          {events.length === 0 ? <p className="muted">Nothing yet. Changes made in Studio, the API and the CLI appear here.</p> : (
            <ul className="changes">
              {events.slice(0, 7).map((entry, index) => (
                <li key={entry.id || index}>
                  <span><b>{entry.action}</b>{entry.target ? <small>{entry.target}</small> : null}</span>
                  <time>{when(entry.created_at)}</time>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <h2 className="learn-title">Learn the model <span>Six short pages</span></h2>
      <div className="learn">
        {TOPICS.map((item) => (
          <button key={item.key} type="button" className="learn-item" onClick={() => setTopic(item.key)}>
            <Art name={item.key} />
            <b>{item.title}</b>
            <small>{item.line}</small>
          </button>
        ))}
      </div>

      {open && (
        <Sheet title={open.title} subtitle={open.line} onClose={() => setTopic(null)} footer={<button type="button" className="quiet" onClick={() => setTopic(null)}>Close</button>}>
          <div className="topic">
            <Art name={open.key} className="topic-art" />
            <p>{open.body}</p>
            <div className="docs-code">
              <pre><code>{open.code}</code></pre>
              <button type="button" onClick={() => copy(open.code)}>Copy</button>
            </div>
            <div className="docs-more">
              <span>Open</span>
              {open.more.map(([label, page]) => <Link key={page} href={hrefFor(page)}>{label}</Link>)}
            </div>
          </div>
        </Sheet>
      )}
    </section>
  );
}
