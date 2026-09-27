import { Link } from "@inertiajs/react";
import { useEffect, useRef, useState } from "react";
import Chart, { Sparkline, compact } from "../../components/Chart";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Card, PageHead, Segmented, when } from "../../components/ui";
import { get } from "../../lib/api";

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

export default function Overview({ project, env, overview }) {
  const [range, setRange] = useState(overview.analytics?.range || "24h");
  const [stats, setStats] = useState(overview.analytics);
  const [loading, setLoading] = useState(false);
  const first = useRef(true);

  useEffect(() => {
    let alive = true;
    async function load() {
      setLoading(true);
      try {
        const data = await get(`/projects/${project.ref}/envs/${env}/analytics`, { params: { range } });
        if (alive) setStats(data);
      } catch {
        /* keep what's on screen */
      } finally {
        if (alive) setLoading(false);
      }
    }
    if (first.current) first.current = false;
    else load();
    const timer = setInterval(load, range === "1h" ? 30000 : 60000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [range, project.ref, env]);

  const s = stats?.summary || {};
  const p = stats?.previous || {};
  const series = stats?.series || [];
  const step = stats?.step_seconds || 3600;
  const href = (section) => envHref(project.ref, env, section);

  return (
    <Layout title={`${project.name} · ${env}`}>
      <PageHead
        title="Overview"
        description={`${project.name} · ${env} · definitions v${overview.version}`}
        actions={
          <div className="row" style={{ gap: 10 }}>
            {loading && <span className="live-dot busy" title="Refreshing" />}
            {!loading && <span className="live-dot" title="Live: refreshes automatically" />}
            <Segmented options={RANGES} value={range} onChange={setRange} />
          </div>
        }
      />

      {overview.problems?.length > 0 && (
        <div className="alert warn" style={{ marginBottom: 16 }}>
          <b>Definitions with problems</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{overview.problems.map((x, i) => <li key={i}>{typeof x === "string" ? x : JSON.stringify(x)}</li>)}</ul>
        </div>
      )}

      <div className="kpis">
        <Kpi label="API requests" value={compact(s.requests)} now={s.requests} before={p.requests} spark={series.map((b) => b.requests)} color={C.requests} />
        <Kpi label="Error rate" value={`${(s.error_rate ?? 0).toFixed(s.error_rate >= 10 ? 0 : 1)}%`} now={s.error_rate} before={p.error_rate} lowerIsBetter spark={series.map((b) => b.errors)} color={C.errors} sub={`${compact(s.errors)} server errors`} />
        <Kpi label="Avg latency" value={`${compact(s.avg_latency_ms)} ms`} now={s.avg_latency_ms} before={p.avg_latency_ms} lowerIsBetter spark={series.map((b) => b.latency_ms)} color={C.latency} />
        <Kpi label="Flow runs" value={compact(s.flow_runs)} now={s.flow_runs} before={p.flow_runs} spark={series.map((b) => b.flow_runs)} color={C.events} sub={s.flow_success_rate == null ? "no runs yet" : `${s.flow_success_rate}% succeeded`} />
      </div>

      <Card
        title="Traffic"
        className="chart-card"
        actions={<Legend items={[["Requests", C.requests], ["4xx", C.client], ["5xx", C.errors]]} />}
      >
        <Chart
          height={280}
          stepSeconds={step}
          data={series}
          series={[
            { key: "requests", label: "Requests", color: C.requests, type: "area" },
            { key: "client_errors", label: "Client errors (4xx)", color: C.client, type: "line", dashed: true },
            { key: "errors", label: "Server errors (5xx)", color: C.errors, type: "line" },
          ]}
          empty="No API traffic in this range. Requests through the gateway appear here."
        />
      </Card>

      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="Latency" className="chart-card" actions={<span className="muted small">average per {step < 3600 ? "5 min" : step < 86400 ? `${step / 3600}h` : "day"}</span>}>
          <Chart
            height={200}
            stepSeconds={step}
            data={series}
            format={(v) => `${compact(v)}`}
            series={[{ key: "latency_ms", label: "Avg latency (ms)", color: C.latency, type: "line" }]}
            empty="No requests to measure yet."
          />
        </Card>
        <Card title="Automation" className="chart-card" actions={<Legend items={[["Events", C.events], ["Flow runs", C.runs], ["Failed", C.failures]]} />}>
          <Chart
            height={200}
            stepSeconds={step}
            data={series.map((b) => ({ ...b, ok_runs: b.flow_runs - b.flow_failures }))}
            series={[
              { key: "ok_runs", label: "Flow runs", color: C.runs, type: "bar" },
              { key: "flow_failures", label: "Failed runs", color: C.failures, type: "bar" },
              { key: "events", label: "Events", color: C.events, type: "line" },
            ]}
            empty="No events or flow runs in this range."
          />
        </Card>
      </div>

      <div className="grid three insights" style={{ marginTop: 14 }}>
        <Card title="Top events" actions={<Link className="link-more" href={href("events")}>All events <Icon name="chevronRight" size={14} /></Link>}>
          <Ranked rows={stats?.top_events || []} color={C.events} empty="No events yet." />
        </Card>
        <Card title="Busiest flows" actions={<Link className="link-more" href={href("flows")}>Flows <Icon name="chevronRight" size={14} /></Link>}>
          <Ranked rows={stats?.top_flows || []} color={C.runs} empty="No flow runs yet." />
        </Card>
        <Card title="Recent failures" actions={<Link className="link-more" href={href("events")}>Runs <Icon name="chevronRight" size={14} /></Link>}>
          {(stats?.recent_failures || []).length === 0 ? (
            <div className="calm"><span className="calm-icon"><Icon name="check" size={18} /></span>No failed runs in the {RANGE_NAMES[range]}.</div>
          ) : (
            <ul className="failures">
              {stats.recent_failures.map((f) => (
                <li key={f.id}>
                  <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
                    <b className="mono">{f.flow}</b>
                    <span className="faint small">{when(f.at)}</span>
                  </div>
                  <span className="muted small clamp">{f.error || "failed"}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </Layout>
  );
}

function Kpi({ label, value, sub, now, before, lowerIsBetter, spark, color }) {
  let delta = null;
  if (before > 0 && now != null) delta = ((now - before) / before) * 100;
  else if (!before && now > 0) delta = null;
  const good = delta == null ? null : lowerIsBetter ? delta <= 0 : delta >= 0;
  return (
    <div className="kpi card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="kpi-label">{label}</span>
        {delta != null && Math.abs(delta) >= 0.5 && (
          <span className={`delta ${good ? "good" : "bad"}`}>
            <Icon name={delta >= 0 ? "arrowUp" : "arrowDown"} size={12} />
            {Math.abs(delta) >= 100 ? Math.round(Math.abs(delta)) : Math.abs(delta).toFixed(1)}%
          </span>
        )}
      </div>
      <div className="kpi-value">{value}</div>
      <span className="kpi-sub">{sub || "vs previous period"}</span>
      <Sparkline values={spark} color={color} />
    </div>
  );
}

function Legend({ items }) {
  return (
    <div className="legend">
      {items.map(([label, color]) => <span key={label}><i className="swatch" style={{ background: color }} />{label}</span>)}
    </div>
  );
}

function Ranked({ rows, color, empty }) {
  if (!rows.length) return <p className="muted small" style={{ margin: 0 }}>{empty}</p>;
  const peak = Math.max(...rows.map((r) => r.count));
  return (
    <ul className="ranked">
      {rows.map((r) => (
        <li key={r.name}>
          <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
            <span className="mono clip">{r.name}</span>
            <b>{compact(r.count)}</b>
          </div>
          <span className="bar"><span style={{ width: `${Math.max(3, (r.count / peak) * 100)}%`, background: color }} /></span>
        </li>
      ))}
    </ul>
  );
}
