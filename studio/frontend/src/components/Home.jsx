import { Link, usePage } from "@inertiajs/react";
import { useState } from "react";
import { envPath, useApi } from "../lib/api";

const STATE = {
  in_sync: ["In sync", "ok"],
  pending: ["Not applied", "warn"],
  review: ["Needs review", "warn"],
  external: ["Changed outside", "warn"],
  no_table: ["No table yet", "warn"],
};

/** The home of an environment: what is in it, and how to reach it. Nothing else. */
export default function Home({ env }) {
  const { gateway_url: gatewayUrl } = usePage().props;
  const [copied, setCopied] = useState(false);
  const resources = useApi(envPath(env, "/resources"));
  const schema = useApi(envPath(env, "/schema"));

  const list = resources.data?.data ?? [];
  const states = Object.fromEntries((schema.data?.resources || []).map((item) => [item.resource, item.state]));
  const copy = () => {
    try { navigator.clipboard?.writeText(gatewayUrl); } catch { /* visible to select */ }
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <section className="home3" aria-label="Environment">
      <header className="home3-head">
        <h1>{env}</h1>
        <Link href={`/envs/${env}/resources`} className="pill dark">New collection</Link>
      </header>

      {resources.loading && !resources.data ? null : list.length === 0 ? (
        <div className="home3-empty">
          <p>No collections yet.</p>
          <span>A collection is a table with an API. Create one to get started.</span>
        </div>
      ) : (
        <ul className="home3-list">
          {list.map((item) => {
            const [label, tone] = STATE[states[item.name]] || [null, null];
            return (
              <li key={item.name}>
                <Link href={`/envs/${env}/resources?resource=${encodeURIComponent(item.name)}`}>
                  <b>{item.name}</b>
                  <span className="grow" />
                  {label && <i className={`state ${tone}`}>{label}</i>}
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      <footer className="home3-connect">
        <span>API endpoint</span>
        <code>{gatewayUrl || "—"}</code>
        {gatewayUrl && <button type="button" onClick={copy}>{copied ? "Copied" : "Copy"}</button>}
        <Link href={`/envs/${env}/keys`}>API keys</Link>
      </footer>
    </section>
  );
}
