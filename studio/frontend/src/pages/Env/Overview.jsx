import { Link, usePage } from "@inertiajs/react";
import { useEffect, useRef, useState } from "react";
import Chart, { compact } from "../../components/Chart";
import DateRange, { formatDay, rangeParams } from "../../components/DateRange";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, IdChip, PageHead, Segmented, when } from "../../components/ui";
import { envPath, get, useApi } from "../../lib/api";

const RANGES = [["1h", "1h"], ["24h", "24h"], ["7d", "7d"], ["30d", "30d"]];
const RANGE_NAMES = { "1h": "last hour", "24h": "last 24 hours", "7d": "last 7 days", "30d": "last 30 days" };

const C = {
  requests: "var(--brand)",
  errors: "var(--danger)",
  client: "var(--warn)",
  latency: "var(--info)",
  events: "var(--chart-mint)",
  runs: "var(--brand)",
  failures: "var(--danger)",
};

const TABS = [
  { value: "traffic", label: "Traffic" },
  { value: "latency", label: "Latency" },
  { value: "automation", label: "Automation" },
];

const statusTone = (status) => (status >= 500 ? "red" : status >= 400 ? "yellow" : "green");
const duration = (value) => {
  const n = Number(value || 0);
  return n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`;
};

/** The last requests through the gateway, refreshed every 10 seconds; each opens its trace in Activity. */
function RecentRequests({ env }) {
  const requests = useApi(`${envPath(env)}/requests`, { params: { limit: 8 }, interval: 10000 });
  const rows = requests.data?.data || [];
  return (
    <div className="ov-panel flex flex-col gap-3 min-w-0 p-[18px_var(--gutter)_20px] [border-right:1px_solid_var(--line)] [&:last-child]:border-r-0 [&.flush]:p-0 [&.flush]:gap-0 [&_p]:m-0 max-layout:border-r-0 max-layout:[border-bottom:1px_solid_var(--line)] flush recent-requests flex flex-col min-h-0">
      <div className="ov-panel-head flex items-center justify-between gap-2 [&.pad]:min-h-[52px] [&.pad]:p-[0_var(--gutter)] [&.pad]:[border-bottom:1px_solid_var(--line)] [&_h2]:text-base [&_h2]:font-semibold pad p-[14px_var(--gutter)] m-0"><h2>Recent requests</h2><Link className="link-more inline-flex items-center gap-0.5 text-ui-sm font-semibold text-muted [&:hover]:text-text" href={`${envHref(env, "activity")}#requests`}>View all <Icon name="chevronRight" size={14} /></Link></div>
      {requests.loading && !requests.data ? <p className="muted text-muted small text-ui-sm pad p-[14px_var(--gutter)] m-0">Loading…</p> : rows.length === 0 ? (
        <div className="recent-empty flex-1 flex flex-col items-center justify-center gap-2 p-[32px_var(--gutter)] text-faint text-center [&_p]:m-0 [&_p]:text-ui-sm [&_p]:text-muted [&_p]:max-w-[260px]"><Icon name="pulse" size={18} /><p>No requests yet. Calls through the gateway appear here as they happen.</p></div>
      ) : (
        <ul className="recent-list list-none m-0 p-0 overflow-y-auto [&_li_a]:grid [&_li_a]:grid-cols-[52px_minmax(0,_1fr)_auto] [&_li_a]:grid-rows-[auto_auto] [&_li_a]:gap-x-2 [&_li_a]:gap-y-0.5 [&_li_a]:items-center [&_li_a]:p-[9px_var(--gutter)] [&_li_a]:[border-bottom:1px_solid_var(--line)] [&_li_a]:text-text [&_li:last-child_a]:border-b-0 [&_li_a:hover]:bg-hover [&_.method]:[font:600_11px_var(--mono)] [&_.method]:text-muted [&_.path]:text-xs [&_.path]:overflow-hidden [&_.path]:text-ellipsis [&_.path]:whitespace-nowrap [&_.meta]:col-[2_/_-1] [&_.meta]:text-caption [&_.meta]:text-faint">
          {rows.map((r) => (
            <li key={r.request_id || `${r.started_at}${r.path}`}>
              <Link href={`${envHref(env, "activity")}#requests`} title={r.request_id}>
                <span className="method">{r.method}</span>
                <code className="path">{r.path}</code>
                <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                <span className="meta">{duration(r.duration_ms)} · {when(r.started_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Metric({ label, value, sub, href, bad }) {
  return (
    <Link href={href} className="metric relative flex flex-col gap-1 min-h-0 p-[16px_var(--gutter)_18px] border-0 [border-right:1px_solid_var(--line)] rounded-none bg-transparent text-text shadow-none [&:last-child]:border-r-0 [&_b]:text-display-sm [&_b]:leading-[1.2] [&_b]:font-semibold [&_b]:tracking-[-0.02em] [&_b]:text-text [&_b]:tabular-nums [&_span]:text-ui-sm [&_span]:font-medium [&_span]:text-muted [&_span]:max-w-none [a&:hover]:bg-hover max-navigation:[border-bottom:1px_solid_var(--line)] [&_span]:order-[0] [&_b]:order-[1] [&_small]:order-[2] [&_small]:text-xs [&_small]:text-faint [&_small.bad]:text-danger">
      <span>{label}</span>
      <b>{value ?? "—"}</b>
      {sub && <small className={bad ? "bad" : ""}>{sub}</small>}
    </Link>
  );
}


export default function Overview({ runtime, env, overview }) {
  const { gateway_url: gatewayUrl, envs } = usePage().props;
  const projectId = (envs || []).find((e) => e.name === env)?.project_id;
  const [range, setRange] = useState(overview.analytics?.range || "24h");
  const [custom, setCustom] = useState(null);
  const [tab, setTab] = useState("traffic");
  const [stats, setStats] = useState(overview.analytics);
  const [loading, setLoading] = useState(false);
  const first = useRef(true);
  const keys = useApi(envPath(env, "/keys"));
  const day = overview.last_24h || {};

  useEffect(() => {
    let alive = true;
    async function load() {
      setLoading(true);
      try {
        const params = custom ? rangeParams(custom) : { range };
        const data = await get(`/envs/${env}/analytics`, { params });
        if (alive) setStats(data);
      } catch {
        /* keep what's on screen */
      } finally {
        if (alive) setLoading(false);
      }
    }
    if (first.current) first.current = false;
    else load();
    const timer = custom ? null : setInterval(load, range === "1h" ? 30000 : 60000);
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [range, custom, env]);

  const s = stats?.summary || {};
  const series = stats?.series || [];
  const step = stats?.step_seconds || 3600;
  const stepName = step < 3600 ? `${Math.round(step / 60)} min` : step < 86400 ? `${step / 3600} h` : "day";
  const span = custom ? (custom[0].getTime() === custom[1].getTime() ? formatDay(custom[0]) : `${formatDay(custom[0])} – ${formatDay(custom[1])}`) : RANGE_NAMES[range];

  return (
    <Layout title={`${runtime.name} · ${env}`}>
      <PageHead
        title="Overview"
        description={`${runtime.name} · ${env} · definitions v${overview.version}`}
        actions={
          <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 10, justifyContent: "flex-end" }}>
            {loading ? <span className="live-dot w-2 h-2 rounded-full bg-ok shadow-none [&.busy]:bg-brand [&.busy]:animate-[blink_0.9s_ease-in-out_infinite] [&.off]:bg-[var(--faint)] [&.off]:shadow-none busy" title="Refreshing" /> : <span className={`live-dot w-2 h-2 rounded-full bg-ok shadow-none [&.busy]:bg-brand [&.busy]:animate-[blink_0.9s_ease-in-out_infinite] [&.off]:bg-[var(--faint)] [&.off]:shadow-none ${custom ? "off" : ""}`} title={custom ? "A fixed range does not refresh" : "Live: refreshes automatically"} />}
            <Segmented options={RANGES} value={custom ? null : range} onChange={(value) => { setCustom(null); setRange(value); }} />
            <DateRange value={custom} active={Boolean(custom)} onApply={setCustom} />
          </div>
        }
      />

      {/* What identifies this environment, on a quiet line of its own so the head keeps room for the title and the range. */}
      <div className="ov-ids flex flex-wrap items-center gap-2.5" style={{ padding: "12px var(--gutter)", borderBottom: "1px solid var(--line)" }}>
        <IdChip label="Project ID" value={projectId} />
        <IdChip label="Service URL" value={gatewayUrl} />
      </div>

      {overview.problems?.length > 0 && (
        <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] warn">
          <b>Definitions with problems</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{overview.problems.map((x, i) => <li key={i}>{typeof x === "string" ? x : JSON.stringify(x)}</li>)}</ul>
        </div>
      )}

      <div className="ov-board [&_>_.metrics]:order-[0] [&_>_.metrics]:[border-bottom:1px_solid_var(--line-2)] [&_>_.metrics_.metric]:pl-[18px] [&_>_.metrics_.metric]:pr-[18px] [&_>_.metrics_.metric]:border-r-line-2 [&_>_.ov-grid]:[border-bottom-color:var(--line-2)] [&_>_.ov-grid:last-child]:border-b-0 [&_>_.ov-grid_>_.ov-panel]:border-r-line-2 [&_.chart-panel]:pl-[18px] [&_.chart-panel]:pr-[18px] [&_.recent-requests_.ov-panel-head.pad]:p-[0_18px] [&_.recent-requests_.ov-panel-head.pad]:border-b-line-2 max-layout:[&_>_.ov-grid.chart-split_>_.chart-panel]:border-r-0 max-layout:[&_>_.ov-grid.chart-split_>_.chart-panel]:[border-bottom:1px_solid_var(--line-2)] shadow-none [&_*]:shadow-none">
      <div className="metrics order-[-1] grid grid-cols-[repeat(auto-fit,_minmax(170px,_1fr))] gap-0 m-0 p-0 [border-bottom:1px_solid_var(--line)] bg-transparent">
        <Metric label={`Requests, ${span}`} value={compact(s.requests)} sub={`${(s.error_rate ?? 0).toFixed(s.error_rate >= 10 ? 0 : 1)}% server errors`} href={envHref(env, "activity")} bad={s.error_rate >= 5} />
        <Metric label="Average latency" value={s.avg_latency_ms != null ? `${compact(s.avg_latency_ms)} ms` : "—"} sub="across gateway requests" href={envHref(env, "activity")} />
        <Metric label="Flow runs, 24h" value={compact(day.flow_runs)} sub={day.flow_failures ? `${compact(day.flow_failures)} failed` : "none failed"} href={envHref(env, "flows")} bad={day.flow_failures > 0} />
        <Metric label="Jobs, 24h" value={compact(day.jobs)} sub={day.job_failures ? `${compact(day.job_failures)} failed` : "none failed"} href={envHref(env, "jobs")} bad={day.job_failures > 0} />
        <Metric label="Events, 24h" value={compact(day.events)} sub={`${compact(day.mail)} emails`} href={envHref(env, "events")} />
      </div>

      <section className="ov-grid grid gap-0 [border-bottom:1px_solid_var(--line)] [&.three]:grid-cols-3 [&.two]:grid-cols-2 max-layout:[&.three]:grid-cols-1 max-layout:[&.two]:grid-cols-1 [&.chart-split]:grid-cols-[minmax(0,_1fr)_380px] max-layout:[&.chart-split]:grid-cols-1 chart-split">
      <div className="ov-panel flex flex-col gap-3 min-w-0 p-[18px_var(--gutter)_20px] [border-right:1px_solid_var(--line)] [&:last-child]:border-r-0 [&.flush]:p-0 [&.flush]:gap-0 [&_p]:m-0 max-layout:border-r-0 max-layout:[border-bottom:1px_solid_var(--line)] chart-panel p-[14px_var(--gutter)_16px] gap-2 [&_.chart-top]:mb-0 [&_.tabs]:mb-0 [&_.chart-sum]:m-[2px_0_0] flex flex-col [&_>_.chart.fill]:flex-1 [&_>_.chart.fill]:min-h-[220px] [&_>_.chart.fill]:h-auto">
        <div className="chart-top flex items-center justify-between gap-3 flex-wrap mb-3.5 [&_.tabs]:mb-0">
          <Segmented options={TABS.map((t) => [t.value, t.label])} value={tab} onChange={setTab} />
          <span className="muted text-muted small text-ui-sm">{span} · per {stepName}</span>
        </div>
        {tab === "traffic" && (
          <>
            <p className="chart-sum flex items-baseline gap-1.5 flex-wrap m-[0_0_8px] text-muted text-body [&_b]:text-text [&_b]:text-display-sm [&_b]:font-bold-plus [&_b]:tracking-[-0.03em] [&_b_+_b]:text-heading [&_b:not(:first-child)]:text-heading"><b>{compact(s.requests)}</b> requests · <b>{(s.error_rate ?? 0).toFixed(s.error_rate >= 10 ? 0 : 1)}%</b> server errors<Legend items={[["Requests", C.requests], ["4xx", C.client], ["5xx", C.errors]]} /></p>
            <Chart
              height="fill"
              stepSeconds={step}
              data={series}
              series={[
                { key: "requests", label: "Requests", color: C.requests, type: "area" },
                { key: "client_errors", label: "Client errors (4xx)", color: C.client, type: "line", dashed: true },
                { key: "errors", label: "Server errors (5xx)", color: C.errors, type: "line" },
              ]}
              empty="No API traffic in this range. Requests through the gateway appear here."
            />
          </>
        )}
        {tab === "latency" && (
          <>
            <p className="chart-sum flex items-baseline gap-1.5 flex-wrap m-[0_0_8px] text-muted text-body [&_b]:text-text [&_b]:text-display-sm [&_b]:font-bold-plus [&_b]:tracking-[-0.03em] [&_b_+_b]:text-heading [&_b:not(:first-child)]:text-heading"><b>{compact(s.avg_latency_ms)} ms</b> average latency across requests</p>
            <Chart height="fill" stepSeconds={step} data={series} format={(v) => `${compact(v)} ms`} series={[{ key: "latency_ms", label: "Average latency", color: C.latency, type: "bar" }]} empty="No requests to measure yet." />
          </>
        )}
        {tab === "automation" && (
          <>
            <p className="chart-sum flex items-baseline gap-1.5 flex-wrap m-[0_0_8px] text-muted text-body [&_b]:text-text [&_b]:text-display-sm [&_b]:font-bold-plus [&_b]:tracking-[-0.03em] [&_b_+_b]:text-heading [&_b:not(:first-child)]:text-heading"><b>{compact(s.flow_runs)}</b> flow runs{s.flow_success_rate != null && <> · <b>{s.flow_success_rate}%</b> succeeded</>} · <b>{compact(s.events)}</b> events<Legend items={[["Events", C.events], ["Flow runs", C.runs], ["Failed", C.failures]]} /></p>
            <Chart
              height="fill"
              stepSeconds={step}
              data={series.map((b) => ({ ...b, ok_runs: b.flow_runs - b.flow_failures }))}
              series={[
                { key: "ok_runs", label: "Flow runs", color: C.runs, type: "bar" },
                { key: "flow_failures", label: "Failed runs", color: C.failures, type: "bar" },
                { key: "events", label: "Events", color: C.events, type: "line" },
              ]}
              empty="No events or flow runs in this range."
            />
          </>
        )}
      </div>
      <RecentRequests env={env} />
      </section>
      </div>

      <div className="ov-board flex flex-col">
        <div className="flex items-center justify-between gap-3 h-[56px] p-[0_var(--gutter)] [border-bottom:1px_solid_var(--line)]">
          <h2 className="m-0 text-base font-semibold">API keys</h2>
          <Link className="btn inline-flex items-center justify-center gap-1.5 h-control-sm p-[0_10px] rounded-sm bg-transparent text-text text-ui-sm font-medium-plus whitespace-nowrap [border:1px_solid_var(--line-2)] cursor-pointer shadow-none [&:hover]:bg-hover [&:hover]:border-line-hover" href={envHref(env, "keys")}><Icon name="keys" size={14} /> Manage keys</Link>
        </div>
        {keys.loading ? (
          <p className="m-0 p-[16px_var(--gutter)] text-ui-sm text-muted">Loading…</p>
        ) : (keys.data?.data || []).length === 0 ? (
          <p className="m-0 p-[16px_var(--gutter)] text-ui-sm text-muted">No keys yet. Create one to call this environment from an application.</p>
        ) : (
          <table className="w-full border-collapse text-ui">
            <thead>
              <tr className="[&_th]:h-9 [&_th]:p-[0_var(--gutter)] [&_th]:text-left [&_th]:align-middle [&_th]:text-xs [&_th]:font-medium-plus [&_th]:text-muted [&_th]:whitespace-nowrap [&_th]:[border-bottom:1px_solid_var(--line)]">
                <th>Name</th>
                <th>Key</th>
                <th>Role</th>
                <th>Last used</th>
              </tr>
            </thead>
            <tbody>
              {keys.data.data.filter((k) => k.active).slice(0, 8).map((k) => (
                <tr key={k.id} className="[&_td]:h-[48px] [&_td]:p-[0_var(--gutter)] [&_td]:align-middle [&_td]:[border-bottom:1px_solid_var(--line)] [&:last-child_td]:border-b-0 [&:hover_td]:bg-hover">
                  <td className="font-semibold text-text">{k.name}</td>
                  <td className="font-mono text-xs text-muted">{k.prefix}…</td>
                  <td><Badge tone={k.role === "secret" ? "yellow" : "blue"}>{k.role}</Badge></td>
                  <td className="whitespace-nowrap text-ui-sm text-muted">{k.last_used_at ? when(k.last_used_at) : "never"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Layout>
  );
}

function Legend({ items }) {
  return (
    <span className="legend flex flex-wrap gap-3.5 text-xs text-muted [&_span]:inline-flex [&_span]:items-center [&_span]:gap-1.5 mt-2 [&_i]:inline-block [&_i]:w-2.5 [&_i]:h-2.5 [&_i]:rounded-xs [&_i]:mr-1" style={{ marginLeft: "auto" }}>
      {items.map(([label, color]) => <span key={label}><i className="swatch w-2.5 h-2.5 rounded-xs inline-block flex-none" style={{ background: color }} />{label}</span>)}
    </span>
  );
}
