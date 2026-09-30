import { useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, CopyText, Empty, Field, Json, Loading, Modal, PageHead, Status, Table, useAction, when } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";

export default function Observability({ project, env }) {
  const base = envPath(project.ref, env);
  const [minutes, setMinutes] = useState(60);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [requestId, setRequestId] = useState("");
  const [detail, setDetail] = useState(null);
  const metrics = useApi(`${base}/metrics`, { params: { minutes }, interval: 15000 });
  const requests = useApi(`${base}/requests`, { params: { status, search, request_id: requestId, limit: 100 }, interval: 10000 });
  const cache = useApi("/cache");
  const series = useMemo(() => {
    const out = {};
    for (const row of metrics.data?.data || []) (out[row.name] ||= []).push(row);
    return out;
  }, [metrics.data]);
  return (
    <Layout title="Observability">
      <PageHead title="Observability" description="Trace a request from HTTP entry through policies, flows, events, user logs, and every executed flow step."
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
        <Card flush title={<div className="row wrap">
          <input placeholder="Search path" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 220 }} />
          <input placeholder="Exact request id" value={requestId} onChange={(e) => setRequestId(e.target.value)} style={{ width: 250 }} />
          <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 150 }}><option value="">every status</option><option value="2xx">2xx success</option><option value="4xx">4xx client error</option><option value="5xx">5xx server error</option></select>
        </div>}>
          <Loading state={requests}>
            {(data) => <Table rows={data.data || []} onRowClick={async (r) => setDetail(await get(`${base}/requests/${encodeURIComponent(r.request_id)}`))} columns={[
              { label: "When", render: (r) => when(r.started_at) },
              { label: "Method", render: (r) => <Badge tone="blue">{r.method}</Badge> },
              { label: "Path", render: (r) => <code>{r.path}</code> },
              { label: "Status", render: (r) => <Badge tone={r.status >= 500 ? "red" : r.status >= 400 ? "yellow" : "green"}>{r.status}</Badge> },
              { label: "Duration", render: (r) => `${Math.round(r.duration_ms || 0)} ms` },
              { label: "Caller", render: (r) => r.user || r.role || "anonymous" },
              { label: "Request id", render: (r) => <code className="faint">{r.request_id}</code> },
            ]} />}
          </Loading>
        </Card>
        <Card title="Cache" actions={<InvalidateCache base={base} />}>
          <Loading state={cache}>{(data) => <pre className="code-block">{JSON.stringify(data, null, 2)}</pre>}</Loading>
        </Card>
      </div>
      {detail && <RequestTrace value={detail} onClose={() => setDetail(null)} />}
    </Layout>
  );
}

function RequestTrace({ value, onClose }) {
  const summary = value.summary || {};
  return (
    <Modal title="Request trace" onClose={onClose}>
      <div className="stack lg">
        <CopyText text={value.request_id} />
        <div className="row wrap">
          <Badge tone={summary.failed ? "red" : "green"}>{summary.failed ? "failed" : "completed"}</Badge>
          <span>{Math.round(summary.duration_ms || 0)} ms</span>
          <span>{summary.flows || 0} flows</span>
          <span>{summary.events || 0} events</span>
          <span>{summary.jobs || 0} jobs</span>
          <span>{summary.logs || 0} user logs</span>
        </div>

        <TraceSection title="HTTP request">
          {(value.requests || []).map((request) => (
            <Card key={request.id} title={<span><Badge tone="blue">{request.method}</Badge> <code>{request.path}</code></span>} actions={<Badge tone={request.status >= 500 ? "red" : request.status >= 400 ? "yellow" : "green"}>{request.status}</Badge>}>
              <div className="stack">
                <div className="faint">{request.route || "unmatched route"} · {request.duration_ms} ms · {request.started_at}</div>
                <div>Caller: <code>{request.user || request.role || "anonymous"}</code> · IP: <code>{request.ip || "unknown"}</code></div>
                {request.error && <div className="alert error">{request.error}</div>}
                <Json value={{ user_agent: request.user_agent, notes: request.notes }} />
              </div>
            </Card>
          ))}
        </TraceSection>

        <TraceSection title={`User logs (${(value.logs || []).length})`} empty={!value.logs?.length}>
          {(value.logs || []).map((log, index) => (
            <Card key={`${log.run_id || "log"}-${log.sequence || index}`} title={<span><Badge tone={log.level === "error" ? "red" : log.level === "warning" ? "yellow" : "blue"}>{log.level || "info"}</Badge> {log.message}</span>} actions={<code>{log.category || "flow"}{log.code ? ` / ${log.code}` : ""}</code>}>
              <div className="faint">{log.timestamp || log.at} · flow {log.flow || "—"} · node {log.node || "—"}</div>
              {(log.data != null || Object.keys(log.tags || {}).length > 0) && <Json value={{ tags: log.tags || {}, data: log.data }} />}
            </Card>
          ))}
        </TraceSection>

        <TraceSection title={`Flow runs (${(value.flow_runs || []).length})`} empty={!value.flow_runs?.length}>
          {(value.flow_runs || []).map((run) => (
            <Card key={run.id} title={run.flow} actions={<Status value={run.status} />}>
              <div className="faint">{run.trigger} · {run.duration_ms} ms · run <code>{run.id}</code></div>
              {run.error && <div className="alert error">{run.error}</div>}
              <details><summary>Input and output</summary><Json value={{ input: run.input, output: run.output }} /></details>
              <details open={run.status === "failed"}><summary>Step trace ({run.trace?.length || 0})</summary><Json value={run.trace || []} /></details>
            </Card>
          ))}
        </TraceSection>

        <TraceSection title={`Events (${(value.events || []).length})`} empty={!value.events?.length}>
          {(value.events || []).map((event) => <Card key={event.id} title={<code>{event.name}</code>}><Json value={event} /></Card>)}
        </TraceSection>

        <TraceSection title={`Jobs (${(value.jobs || []).length})`} empty={!value.jobs?.length}>
          {(value.jobs || []).map((job) => <Card key={job.id} title={job.job} actions={<Status value={job.status} />}><Json value={job} /></Card>)}
        </TraceSection>
      </div>
    </Modal>
  );
}

function TraceSection({ title, empty, children }) {
  return <section className="stack"><h3>{title}</h3>{empty ? <Empty>Nothing recorded.</Empty> : children}</section>;
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
