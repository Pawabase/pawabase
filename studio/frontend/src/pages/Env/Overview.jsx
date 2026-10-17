import { Link, usePage } from "@inertiajs/react";
import { useEffect, useRef, useState } from "react";
import Chart, { compact } from "../../components/Chart";
import DateRange, { formatDay, rangeParams } from "../../components/DateRange";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, CopyText, IdChip, PageHead, Segmented, copyToClipboard, useToast, when } from "../../components/ui";
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

const INFRA = { database: "Database", storage: "Storage", mail: "Mail", cache: "Cache", queue: "Queue", events: "Events" };
const INVENTORY = [
  ["resources", "Resources", "resources"],
  ["routes", "Routes", "routes"],
  ["policies", "Policies", "policies"],
  ["flows", "Flows", "flows"],
  ["schedules", "Schedules", "schedules"],
  ["subscriptions", "Event subscriptions", "subscriptions"],
  ["webhooks", "Webhooks", "webhooks"],
  ["buckets", "Storage buckets", "storage"],
  ["secrets", "Secrets", "secrets"],
  ["keys", "Active API keys", "keys"],
];

const statusTone = (status) => (status >= 500 ? "red" : status >= 400 ? "yellow" : "green");
const duration = (value) => {
  const n = Number(value || 0);
  return n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`;
};

/** The last requests through the gateway, refreshed every 10 seconds; each opens its trace in Observability. */
function RecentRequests({ env }) {
  const requests = useApi(`${envPath(env)}/requests`, { params: { limit: 8 }, interval: 10000 });
  const rows = requests.data?.data || [];
  return (
    <div className="ov-panel flush recent-requests">
      <div className="ov-panel-head pad"><h2>Recent requests</h2><Link className="link-more" href={`${envHref(env, "observability")}#requests`}>View all <Icon name="chevronRight" size={14} /></Link></div>
      {requests.loading && !requests.data ? <p className="muted small pad">Loading…</p> : rows.length === 0 ? (
        <div className="recent-empty"><Icon name="pulse" size={18} /><p>No requests yet. Calls through the gateway appear here as they happen.</p></div>
      ) : (
        <ul className="recent-list">
          {rows.map((r) => (
            <li key={r.request_id || `${r.started_at}${r.path}`}>
              <Link href={`${envHref(env, "observability")}#requests`} title={r.request_id}>
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
    <Link href={href} className="metric">
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
  const advisor = useApi(`${envPath(env)}/advisor`, { interval: 60000 });
  const findings = advisor.data?.findings || [];
  const limits = advisor.data?.limits || [];
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
          <div className="row wrap" style={{ gap: 10, justifyContent: "flex-end" }}>
            <IdChip label="Project ID" value={projectId} />
            <IdChip label="Service URL" value={gatewayUrl} />
            {loading ? <span className="live-dot busy" title="Refreshing" /> : <span className={`live-dot ${custom ? "off" : ""}`} title={custom ? "A fixed range does not refresh" : "Live: refreshes automatically"} />}
            <Segmented options={RANGES} value={custom ? null : range} onChange={(value) => { setCustom(null); setRange(value); }} />
            <DateRange value={custom} active={Boolean(custom)} onApply={setCustom} />
          </div>
        }
      />

      {overview.problems?.length > 0 && (
        <div className="alert warn">
          <b>Definitions with problems</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{overview.problems.map((x, i) => <li key={i}>{typeof x === "string" ? x : JSON.stringify(x)}</li>)}</ul>
        </div>
      )}

      <div className="ov-board">
      <div className="metrics">
        <Metric label={`Requests, ${span}`} value={compact(s.requests)} sub={`${(s.error_rate ?? 0).toFixed(s.error_rate >= 10 ? 0 : 1)}% server errors`} href={envHref(env, "observability")} bad={s.error_rate >= 5} />
        <Metric label="Average latency" value={s.avg_latency_ms != null ? `${compact(s.avg_latency_ms)} ms` : "—"} sub="across gateway requests" href={envHref(env, "observability")} />
        <Metric label="Flow runs, 24h" value={compact(day.flow_runs)} sub={day.flow_failures ? `${compact(day.flow_failures)} failed` : "none failed"} href={envHref(env, "flows")} bad={day.flow_failures > 0} />
        <Metric label="Jobs, 24h" value={compact(day.jobs)} sub={day.job_failures ? `${compact(day.job_failures)} failed` : "none failed"} href={envHref(env, "jobs")} bad={day.job_failures > 0} />
        <Metric label="Events, 24h" value={compact(day.events)} sub={`${compact(day.mail)} emails`} href={envHref(env, "events")} />
      </div>

      <section className="ov-grid chart-split">
      <div className="ov-panel chart-panel">
        <div className="chart-top">
          <Segmented options={TABS.map((t) => [t.value, t.label])} value={tab} onChange={setTab} />
          <span className="muted small">{span} · per {stepName}</span>
        </div>
        {tab === "traffic" && (
          <>
            <p className="chart-sum"><b>{compact(s.requests)}</b> requests · <b>{(s.error_rate ?? 0).toFixed(s.error_rate >= 10 ? 0 : 1)}%</b> server errors<Legend items={[["Requests", C.requests], ["4xx", C.client], ["5xx", C.errors]]} /></p>
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
            <p className="chart-sum"><b>{compact(s.avg_latency_ms)} ms</b> average latency across requests</p>
            <Chart height="fill" stepSeconds={step} data={series} format={(v) => `${compact(v)} ms`} series={[{ key: "latency_ms", label: "Average latency", color: C.latency, type: "bar" }]} empty="No requests to measure yet." />
          </>
        )}
        {tab === "automation" && (
          <>
            <p className="chart-sum"><b>{compact(s.flow_runs)}</b> flow runs{s.flow_success_rate != null && <> · <b>{s.flow_success_rate}%</b> succeeded</>} · <b>{compact(s.events)}</b> events<Legend items={[["Events", C.events], ["Flow runs", C.runs], ["Failed", C.failures]]} /></p>
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

      <section className="ov-grid three">
        <div className="ov-panel">
          <div className="ov-panel-head"><h2>Connect</h2><Link className="link-more" href={envHref(env, "keys")}>API keys <Icon name="chevronRight" size={14} /></Link></div>
          <p className="muted small">Every request goes through the gateway with an <code>apikey</code> header. Publishable keys follow your policies; secret keys bypass them.</p>
          <CopyText text={gatewayUrl} />
          <pre className="code-block ov-snippet">{`curl ${gatewayUrl}/rest/v1/<resource> \\
  -H "apikey: <publishable key>"`}</pre>
          <div className="row wrap">
            <a className="btn sm" href={`/envs/${env}/api-docs`} target="_blank" rel="noreferrer"><Icon name="book" size={14} /> API reference</a>
            <Link className="btn sm" href={envHref(env, "explorer")}><Icon name="explorer" size={14} /> API Explorer</Link>
          </div>
        </div>
        <div className="ov-panel">
          <div className="ov-panel-head"><h2>Advisor</h2><Link className="link-more" href={envHref(env, "observability")}>Observability <Icon name="chevronRight" size={14} /></Link></div>
          {advisor.loading && !advisor.data ? <p className="muted small">Checking…</p> : findings.length === 0 ? (
            <div className="ov-clear"><Icon name="check" size={16} /><span><b>All clear</b><small>Requests, functions, jobs, flows, webhooks and mail were healthy over the last hour, and nothing is close to a limit.</small></span></div>
          ) : (
            <ul className="ov-findings">
              {findings.slice(0, 5).map((f, i) => (
                <li key={i}><span className={`sev ${f.severity}`} /><Link href={envHref(env, f.page || "observability")}><b>{f.title}</b><small>{f.message}</small></Link></li>
              ))}
            </ul>
          )}
          {limits.length > 0 && (
            <div className="ov-limits">
              {limits.map((l) => (
                <Link key={l.key} href={envHref(env, l.page)} className="ov-limit" title={`${l.used.toLocaleString()} of ${l.limit.toLocaleString()}`}>
                  <span className="row spread"><span>{l.label}</span><span className="faint">{compact(l.used)} / {compact(l.limit)}</span></span>
                  <span className="meter"><i className={l.percent >= 100 ? "full" : l.percent >= 80 ? "near" : ""} style={{ width: `${Math.min(100, l.percent)}%` }} /></span>
                </Link>
              ))}
            </div>
          )}
        </div>
        <div className="ov-panel">
          <div className="ov-panel-head"><h2>Infrastructure</h2><Link className="link-more" href={envHref(env, "settings")}>Settings <Icon name="chevronRight" size={14} /></Link></div>
          <dl className="ov-kv">
            {Object.entries(overview.infrastructure || {}).map(([key, value]) => (
              <div key={key}><dt>{INFRA[key] || key}</dt><dd>{String(value)}</dd></div>
            ))}
          </dl>
        </div>
      </section>

      <section className="ov-grid two">
        <div className="ov-panel flush">
          <div className="ov-panel-head pad"><h2>What's built</h2><span className="faint small">definitions v{overview.version}</span></div>
          <div className="ov-inventory">
            {INVENTORY.map(([key, label, page]) => (
              <Link key={key} href={envHref(env, page)} className="ov-count">
                <Icon name={page} size={15} />
                <span className="grow">{label}</span>
                <b>{compact(overview.counts?.[key] ?? 0)}</b>
              </Link>
            ))}
          </div>
        </div>
        <div className="ov-panel flush">
          <div className="ov-panel-head pad"><h2>API keys</h2><Link className="link-more" href={envHref(env, "keys")}>Manage <Icon name="chevronRight" size={14} /></Link></div>
          {keys.loading ? <p className="muted small pad">Loading…</p> : (keys.data?.data || []).length === 0 ? <p className="muted small pad">No keys yet. Create one to call this environment from an application.</p> : (
            <ul className="ov-rows">
              {keys.data.data.filter((k) => k.active).slice(0, 6).map((k) => (
                <li key={k.id}>
                  <div className="grow">
                    <b>{k.name}</b>
                    <span className="muted small"><code>{k.prefix}…</code> · last used {k.last_used_at ? when(k.last_used_at) : "never"}</span>
                  </div>
                  <Badge tone={k.role === "secret" ? "yellow" : "blue"}>{k.role}</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </Layout>
  );
}

function Legend({ items }) {
  return (
    <span className="legend" style={{ marginLeft: "auto" }}>
      {items.map(([label, color]) => <span key={label}><i className="swatch" style={{ background: color }} />{label}</span>)}
    </span>
  );
}
