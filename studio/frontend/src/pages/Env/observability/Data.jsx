import { Link } from "@inertiajs/react";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, Field, Loading, Table, useAction } from "../../../components/ui";
import { envHref } from "../../../components/Layout";
import { envPath, get, post, useApi } from "../../../lib/api";
import { num } from "./system";

// "Round trip" is measured from the browser through Studio to the database; it
// includes the hops, so read it as a trend, not as the database's own latency.
export function Database({ env }) {
  const base = envPath(env);
  const [probe, setProbe] = useState(null);
  const database = useApi(`${base}/database`);
  const overview = useApi(`${base}/overview`, { interval: 30000 });
  const buckets = useApi(`${base}/buckets`);
  useEffect(() => {
    let alive = true;
    const run = async () => {
      const started = performance.now();
      try { await get(`${base}/database`); if (alive) setProbe({ ok: true, ms: Math.round(performance.now() - started) }); }
      catch { if (alive) setProbe({ ok: false }); }
    };
    const timer = setInterval(run, 20000);
    run();
    return () => { alive = false; clearInterval(timer); };
  }, [base]);
  const tables = database.data?.tables || [];
  const managed = tables.filter((t) => t.resource).length;
  const infra = overview.data?.infrastructure || {};
  return (
    <div className="stack lg">
      <Card title="Infrastructure" actions={<Link className="link-more" href={envHref(env, "database")}>Open database console</Link>}>
        <Loading state={overview}>
          {() => <div className="row wrap" style={{ gap: 28 }}>
            {[["Database", `${infra.database}${database.data?.dialect ? ` (${database.data.dialect})` : ""}`], ["Storage", infra.storage], ["Mail", infra.mail], ["Cache", infra.cache], ["Queue", infra.queue], ["Event bus", infra.events]].map(([label, value]) => (
              <span key={label}><span className="muted">{label}</span><div><b>{value || "—"}</b></div></span>
            ))}
          </div>}
        </Loading>
      </Card>
      {overview.data?.problems?.length > 0 && <Card title="Definition problems" actions={<Badge tone="yellow">{overview.data.problems.length}</Badge>}>
        <div className="stack" style={{ gap: 6 }}>{overview.data.problems.slice(0, 10).map((p, i) => <div key={i} className="alert warn">{typeof p === "string" ? p : JSON.stringify(p)}</div>)}</div>
      </Card>}
      <Card flush title="Tables" actions={<span className="muted small">{tables.length} total</span>}>
        <Table rows={tables.map((t) => ({ ...t, id: t.name }))} empty="No tables yet." columns={[
          { label: "Table", render: (t) => <code>{t.name}</code> },
          { label: "Resource", render: (t) => t.resource ? <Badge tone="blue">{t.resource}</Badge> : <span className="faint">unmanaged</span> },
        ]} />
      </Card>
      <Card flush title="Buckets" actions={<Link className="link-more" href={envHref(env, "storage")}>Open storage</Link>}>
        <Loading state={buckets} empty="No buckets defined.">
          {(d) => <Table rows={(d.data || d).map((b) => ({ ...b, id: b.name }))} columns={[
            { label: "Bucket", render: (b) => <b>{b.name}</b> }, { label: "Access", render: (b) => <Badge>{b.access || b.policy || "private"}</Badge> },
            { label: "Max size", render: (b) => b.max_size ? `${Math.round(b.max_size / 1048576)} MB` : "default" },
          ]} />}
        </Loading>
      </Card>
    </div>
  );
}

export function CacheAndMetrics({ env, minutes }) {
  const base = envPath(env);
  const metrics = useApi(`${base}/metrics`, { params: { minutes }, interval: 15000 });
  const cache = useApi("/cache");
  const series = useMemo(() => {
    const out = {};
    for (const row of metrics.data?.data || []) (out[row.name] ||= []).push(row);
    return out;
  }, [metrics.data]);
  const stats = cache.data || {};
  const flat = Object.entries(stats).filter(([, v]) => typeof v === "number" || typeof v === "string");
  return (
    <div className="stack lg">
      <Card title="Cache" actions={<InvalidateCache base={base} />}>
        <Loading state={cache}>
          {() => flat.length
            ? <div className="row wrap" style={{ gap: 28 }}>{flat.map(([k, v]) => <span key={k}><span className="muted">{k.replace(/_/g, " ")}</span><div><b>{typeof v === "number" ? num(v) : v}</b></div></span>)}</div>
            : <pre className="code-block">{JSON.stringify(stats, null, 2)}</pre>}
        </Loading>
      </Card>
      <Loading state={metrics} empty="No metrics recorded in this window.">
        {() => (
          <div className="grid wide">
            {Object.entries(series).map(([name, rows]) => {
              const max = Math.max(...rows.map((r) => r.value), 1);
              const total = rows.reduce((s, r) => s + r.value, 0);
              return (
                <Card key={name} title={<code>{name}</code>} actions={<b>{Math.round(total * 100) / 100}</b>}>
                  <div className="bar-chart">{rows.map((r, i) => <div key={i} title={`${r.window}: ${r.value}`} style={{ height: `${(r.value / max) * 100}%` }} />)}</div>
                </Card>
              );
            })}
          </div>
        )}
      </Loading>
    </div>
  );
}

function InvalidateCache({ base }) {
  const [resources, setResources] = useState("");
  const [run, busy] = useAction();
  return (
    <div className="row">
      <Field><input placeholder="resources, comma-separated" value={resources} onChange={(e) => setResources(e.target.value)} style={{ width: 220 }} /></Field>
      <Button size="sm" disabled={busy || !resources} onClick={() => run(() => post(`${base}/cache/invalidate`, { resources: resources.split(",").map((s) => s.trim()).filter(Boolean) }), "Cache invalidated")}>Invalidate</Button>
    </div>
  );
}
