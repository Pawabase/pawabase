import { useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Field, Loading, PageHead, Table, Tabs, useAction, when } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";
import Errors from "./observability/Errors";
import RequestTrace from "./observability/RequestTrace";
import RouteDetail from "./observability/RouteDetail";
import Routes from "./observability/Routes";
import { ms, statusTone } from "./observability/shared";

const WINDOWS = [
  [15, "last 15 minutes"], [60, "last hour"], [360, "last 6 hours"], [1440, "last day"], [10080, "last week"], [20160, "last 2 weeks"],
];

export default function Observability({ project, env }) {
  const base = envPath(project.ref, env);
  const [tab, setTab] = useState("routes");
  const [minutes, setMinutes] = useState(60);
  const [route, setRoute] = useState(null);
  const [detail, setDetail] = useState(null);
  const openTrace = async (requestId) => {
    if (requestId) setDetail(await get(`${base}/requests/${encodeURIComponent(requestId)}`));
  };
  return (
    <Layout title="Observability">
      <PageHead title="Observability" description="Which routes fail or run slow, what went wrong, and every step, query and log inside a single request."
        actions={<select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} style={{ width: 170 }}>{WINDOWS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: "routes", label: "Routes" },
        { value: "errors", label: "Errors" },
        { value: "requests", label: "Requests" },
        { value: "metrics", label: "Metrics and cache" },
      ]} />
      {tab === "routes" && <Routes project={project} env={env} minutes={minutes} onRoute={setRoute} onTrace={openTrace} />}
      {tab === "errors" && <Errors project={project} env={env} minutes={minutes} onRoute={setRoute} onTrace={openTrace} />}
      {tab === "requests" && <Requests base={base} onTrace={openTrace} />}
      {tab === "metrics" && <MetricsAndCache base={base} minutes={minutes} />}
      {route && <RouteDetail project={project} env={env} route={route} minutes={minutes} onClose={() => setRoute(null)} onTrace={openTrace} />}
      {detail && <RequestTrace value={detail} onClose={() => setDetail(null)} />}
    </Layout>
  );
}

function Requests({ base, onTrace }) {
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [requestId, setRequestId] = useState("");
  const [slow, setSlow] = useState(false);
  const requests = useApi(`${base}/requests`, { params: { status, search, request_id: requestId, limit: 100 }, interval: 10000 });
  return (
    <Card flush title={<div className="row wrap">
      <input placeholder="Search path" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 220 }} />
      <input placeholder="Exact request id" value={requestId} onChange={(e) => setRequestId(e.target.value)} style={{ width: 250 }} />
      <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 150 }}><option value="">every status</option><option value="2xx">2xx success</option><option value="4xx">4xx client error</option><option value="5xx">5xx server error</option></select>
      <label className="row"><input type="checkbox" checked={slow} onChange={(e) => setSlow(e.target.checked)} /> slow only (500 ms+)</label>
    </div>}>
      <Loading state={requests}>
        {(data) => <Table rows={(data.data || []).filter((r) => !slow || r.duration_ms >= 500)} onRowClick={(r) => onTrace(r.request_id)} columns={[
          { label: "When", render: (r) => when(r.started_at) },
          { label: "Method", render: (r) => <Badge tone="blue">{r.method}</Badge> },
          { label: "Path", render: (r) => <code>{r.path}</code> },
          { label: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
          { label: "Duration", render: (r) => ms(r.duration_ms) },
          { label: "Steps", render: (r) => r.notes?.spans?.length ?? <span className="faint">—</span> },
          { label: "Caller", render: (r) => r.user || r.role || "anonymous" },
          { label: "Request id", render: (r) => <code className="faint">{r.request_id}</code> },
        ]} />}
      </Loading>
    </Card>
  );
}

function MetricsAndCache({ base, minutes }) {
  const metrics = useApi(`${base}/metrics`, { params: { minutes }, interval: 15000 });
  const cache = useApi("/cache");
  const series = useMemo(() => {
    const out = {};
    for (const row of metrics.data?.data || []) (out[row.name] ||= []).push(row);
    return out;
  }, [metrics.data]);
  return (
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
      <Card title="Cache" actions={<InvalidateCache base={base} />}>
        <Loading state={cache}>{(data) => <pre className="code-block">{JSON.stringify(data, null, 2)}</pre>}</Loading>
      </Card>
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
