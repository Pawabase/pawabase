import { useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Button, Card, Field, Loading, PageHead, Table, useAction } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

export default function Observability({ project, env }) {
  const base = envPath(project.ref, env);
  const [minutes, setMinutes] = useState(60);
  const metrics = useApi(`${base}/metrics`, { params: { minutes }, interval: 15000 });
  const requests = useApi("/requests", { service: "telemetry", params: { project: project.ref, env, limit: 100 }, interval: 10000 });
  const cache = useApi("/cache");
  const series = useMemo(() => {
    const out = {};
    for (const row of metrics.data?.data || []) (out[row.name] ||= []).push(row);
    return out;
  }, [metrics.data]);
  return (
    <Layout title="Observability">
      <PageHead title="Observability" description="Counters recorded by the platform, recent requests with their request ids, and cache statistics."
        actions={<select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} style={{ width: 150 }}><option value={60}>last hour</option><option value={360}>last 6 hours</option><option value={1440}>last day</option><option value={10080}>last week</option></select>} />
      <div className="stack lg">
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
        <Card flush title="Recent requests (API)">
          <Loading state={requests}>
            {(data) => <Table rows={data.requests || []} columns={[
              { label: "Method", key: "method" }, { label: "Path", render: (r) => <code>{r.path}</code> }, { label: "Status", key: "status" },
              { label: "ms", render: (r) => Math.round(r.duration_ms ?? r.ms ?? 0) }, { label: "Request id", render: (r) => <code className="faint">{r.request_id}</code> },
            ]} />}
          </Loading>
        </Card>
        <Card title="Cache" actions={<InvalidateCache base={base} />}>
          <Loading state={cache}>{(data) => <pre className="code-block">{JSON.stringify(data, null, 2)}</pre>}</Loading>
        </Card>
      </div>
    </Layout>
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
