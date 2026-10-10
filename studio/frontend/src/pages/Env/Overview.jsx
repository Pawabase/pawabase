import { Link, usePage } from "@inertiajs/react";
import { useEffect, useRef, useState } from "react";
import Chart, { compact } from "../../components/Chart";
import DateRange, { formatDay, rangeParams } from "../../components/DateRange";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, CopyText, PageHead, Segmented, Tabs, copyToClipboard, useToast, when } from "../../components/ui";
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

function CopyServiceUrl({ url }) {
  const toast = useToast();
  if (!url) return null;
  return (
    <Button size="sm" title={url} onClick={async () => toast((await copyToClipboard(url)) ? "Service URL copied" : "Could not copy. Select it from the API keys section.", "ok")}>
      <Icon name="copy" size={14} /> Copy service URL
    </Button>
  );
}

export default function Overview({ runtime, env, overview }) {
  const { gateway_url: gatewayUrl } = usePage().props;
  const [range, setRange] = useState(overview.analytics?.range || "24h");
  const [custom, setCustom] = useState(null);
  const [tab, setTab] = useState("traffic");
  const [stats, setStats] = useState(overview.analytics);
  const [loading, setLoading] = useState(false);
  const first = useRef(true);
  const keys = useApi(envPath(env, "/keys"));

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
            <CopyServiceUrl url={gatewayUrl} />
            {loading ? <span className="live-dot busy" title="Refreshing" /> : <span className={`live-dot ${custom ? "off" : ""}`} title={custom ? "A fixed range does not refresh" : "Live: refreshes automatically"} />}
            <Segmented options={RANGES} value={custom ? null : range} onChange={(value) => { setCustom(null); setRange(value); }} />
            <DateRange value={custom} active={Boolean(custom)} onApply={setCustom} />
          </div>
        }
      />

      {overview.problems?.length > 0 && (
        <div className="alert warn" style={{ marginBottom: 16 }}>
          <b>Definitions with problems</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{overview.problems.map((x, i) => <li key={i}>{typeof x === "string" ? x : JSON.stringify(x)}</li>)}</ul>
        </div>
      )}

      <Card className="chart-card">
        <div className="chart-top">
          <Tabs tabs={TABS} value={tab} onChange={setTab} />
          <span className="muted small">{span} · per {stepName}</span>
        </div>
        {tab === "traffic" && (
          <>
            <p className="chart-sum"><b>{compact(s.requests)}</b> requests · <b>{(s.error_rate ?? 0).toFixed(s.error_rate >= 10 ? 0 : 1)}%</b> server errors<Legend items={[["Requests", C.requests], ["4xx", C.client], ["5xx", C.errors]]} /></p>
            <Chart
              height={320}
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
            <p className="chart-sum"><b>{compact(s.avg_latency_ms)} ms</b> average latency</p>
            <Chart height={320} stepSeconds={step} data={series} format={(v) => `${compact(v)}`} series={[{ key: "latency_ms", label: "Avg latency (ms)", color: C.latency, type: "line" }]} empty="No requests to measure yet." />
          </>
        )}
        {tab === "automation" && (
          <>
            <p className="chart-sum"><b>{compact(s.flow_runs)}</b> flow runs{s.flow_success_rate != null && <> · <b>{s.flow_success_rate}%</b> succeeded</>} · <b>{compact(s.events)}</b> events<Legend items={[["Events", C.events], ["Flow runs", C.runs], ["Failed", C.failures]]} /></p>
            <Chart
              height={320}
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
      </Card>

      <div className="ov-lists">
        <Card flush title="API keys" actions={<Link className="link-more" href={envHref(env, "keys")}>Manage <Icon name="chevronRight" size={14} /></Link>}>
          {keys.loading ? <p className="muted small pad">Loading…</p> : (keys.data?.data || []).length === 0 ? <p className="muted small pad">No keys yet. Create one to call this environment from an application.</p> : (
            <ul className="ov-rows">
              {keys.data.data.map((k) => (
                <li key={k.id}>
                  <div className="grow">
                    <b>{k.name}</b>
                    <span className="muted small"><code>{k.prefix}…</code> · last used {k.last_used_at ? when(k.last_used_at) : "never"}</span>
                  </div>
                  <Badge tone={k.role === "secret" ? "yellow" : "blue"}>{k.role}</Badge>
                  <Badge tone={k.active ? "green" : "red"}>{k.active ? "active" : "revoked"}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
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
