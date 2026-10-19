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
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
            {d.truncated && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)]">This window has more requests than the {`50,000`} analysed; the newest are shown.</div>}
            <div className="statstrip flex flex-wrap items-baseline gap-[4px_0] p-0 m-[0_0_14px] bg-none rounded-none overflow-visible [&_>_div]:inline-flex [&_>_div]:items-baseline [&_>_div]:gap-[7px] [&_>_div]:p-[0_18px] [&_>_div]:m-0 [&_>_div]:border-0 [&_>_div]:bg-none [&_>_div]:[border-left:1px_solid_var(--line-2)] [&_>_div:first-child]:pl-0 [&_>_div:first-child]:border-l-0 [&_.v]:text-heading [&_.v]:font-bold [&_.v]:tracking-[-0.02em] [&_.v]:tabular-nums [&_b]:text-heading [&_b]:font-bold [&_b]:tracking-[-0.02em] [&_b]:tabular-nums [&_.l]:inline-flex [&_.l]:items-center [&_.l]:gap-1.5 [&_.l]:text-muted [&_.l]:text-ui [&_.l]:font-medium [&_.l]:normal-case [&_span:not(.v)]:inline-flex [&_span:not(.v)]:items-center [&_span:not(.v)]:gap-1.5 [&_span:not(.v)]:text-muted [&_span:not(.v)]:text-ui [&_span:not(.v)]:font-medium [&_span:not(.v)]:normal-case [&_.dot]:w-1.5 [&_.dot]:h-1.5 [&_.v.bad]:text-danger [&_>_div]:min-w-0">
              <Stat label="Requests" value={d.summary.requests} />
              <Stat label="Average" value={ms(d.summary.avg_ms)} />
              <Stat label="p50" value={ms(d.summary.p50_ms)} />
              <Stat label="p95" value={ms(d.summary.p95_ms)} />
              <Stat label="p99" value={ms(d.summary.p99_ms)} />
              <Stat label="Slowest" value={ms(d.summary.max_ms)} />
            </div>

            <Card title="Traffic and failures">
              <Timeline points={d.timeline} />
              <div className="legend flex flex-wrap gap-3.5 text-xs text-muted [&_span]:inline-flex [&_span]:items-center [&_span]:gap-1.5 mt-2 [&_i]:inline-block [&_i]:w-2.5 [&_i]:h-2.5 [&_i]:rounded-xs [&_i]:mr-1 row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap faint text-faint">
                <span><i style={{ background: "var(--lavender)" }} /> ok</span>
                <span><i style={{ background: "var(--warn)" }} /> 4xx</span>
                <span><i style={{ background: "var(--danger)" }} /> 5xx</span>
              </div>
              <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ marginTop: 10 }}>
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
          { label: "Error", render: (r) => (r.error ? <span className="faint text-faint">{r.error.slice(0, 60)}</span> : "") },
        ]} />
      )}
    </Card>
  );
}

function Stat({ label, value }) {
  return <div><span className="v">{value}</span><span className="l">{label}</span></div>;
}
