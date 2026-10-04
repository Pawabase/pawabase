import { Badge, Card, Empty, Loading, Modal, Table, when } from "../../../components/ui";
import { envPath, useApi } from "../../../lib/api";
import { KindBars, MethodBadge, Timeline, ms, statusTone } from "./shared";

export default function RouteDetail({ env, route, minutes, onClose, onTrace }) {
  const base = envPath(env);
  const state = useApi(`${base}/observability/routes/detail`, { params: { method: route.method, route: route.route, minutes } });
  return (
    <Modal title={<span><MethodBadge method={route.method} /> <code>{route.route}</code></span>} onClose={onClose}>
      <Loading state={state}>
        {(d) => (
          <div className="stack lg">
            {d.truncated && <div className="alert">This window has more requests than the {`50,000`} analysed; the newest are shown.</div>}
            <div className="trace-stats">
              <Stat label="Requests" value={d.summary.requests} />
              <Stat label="Average" value={ms(d.summary.avg_ms)} />
              <Stat label="p50" value={ms(d.summary.p50_ms)} />
              <Stat label="p95" value={ms(d.summary.p95_ms)} />
              <Stat label="p99" value={ms(d.summary.p99_ms)} />
              <Stat label="Slowest" value={ms(d.summary.max_ms)} />
            </div>

            <Card title="Traffic and failures">
              <Timeline points={d.timeline} />
              <div className="legend row wrap faint">
                <span><i style={{ background: "var(--lavender)" }} /> ok</span>
                <span><i style={{ background: "var(--warn)" }} /> 4xx</span>
                <span><i style={{ background: "var(--danger)" }} /> 5xx</span>
              </div>
              <div className="row wrap" style={{ marginTop: 10 }}>
                {d.statuses.map((s) => <Badge key={s.status} tone={statusTone(s.status)}>{s.status} × {s.count}</Badge>)}
              </div>
            </Card>

            <Card title="Where the time goes">
              <KindBars items={d.time_by_kind} />
            </Card>

            <Card flush title={`What went wrong (${d.errors.length})`}>
              <Table rows={d.errors} empty="No failures in this window." onRowClick={(g) => onTrace(g.sample_request_id)} columns={[
                { label: "Status", render: (g) => <Badge tone={statusTone(g.status)}>{g.status}</Badge> },
                { label: "Problem", render: (g) => <code>{g.problem}</code> },
                { label: "Count", render: (g) => <b>{g.count}</b> },
                { label: "Users", key: "users" },
                { label: "Last seen", render: (g) => when(g.last_seen) },
              ]} />
            </Card>

            <RequestList title="Slowest requests" rows={d.slowest} onTrace={onTrace} />
            <RequestList title="Recent failures" rows={d.recent_failures} onTrace={onTrace} />
          </div>
        )}
      </Loading>
    </Modal>
  );
}

function RequestList({ title, rows, onTrace }) {
  return (
    <Card flush title={title}>
      {rows.length === 0 ? <Empty>Nothing to show.</Empty> : (
        <Table rows={rows} onRowClick={(r) => onTrace(r.request_id)} columns={[
          { label: "When", render: (r) => when(r.started_at) },
          { label: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
          { label: "Duration", render: (r) => ms(r.duration_ms) },
          { label: "Path", render: (r) => <code>{r.path}</code> },
          { label: "Caller", render: (r) => r.user || r.role || "anonymous" },
          { label: "Error", render: (r) => (r.error ? <span className="faint">{r.error.slice(0, 60)}</span> : "") },
        ]} />
      )}
    </Card>
  );
}

function Stat({ label, value }) {
  return <div className="trace-stat"><div className="faint">{label}</div><div className="trace-stat-value">{value}</div></div>;
}
