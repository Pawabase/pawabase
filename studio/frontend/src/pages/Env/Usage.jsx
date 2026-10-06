import Layout from "../../components/Layout";
import { Badge, Card, Loading, PageHead } from "../../components/ui";
import { useApi } from "../../lib/api";

const fmt = (n) => Number(n || 0).toLocaleString();
const reset = (s) => !s ? "—" : s >= 86400 ? `resets in ${Math.ceil(s / 86400)}d` : `resets in ${Math.ceil(s / 3600)}h`;
const ratio = (used, limit) => limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
const tone = (value, limit) => !limit ? "blue" : value >= 90 ? "red" : value >= 70 ? "yellow" : "green";

function Gauge({ label, used = 0, limit = 0, caption, icon }) {
  const value = ratio(used, limit), size = 264;
  return <Card>
    <div className="spread"><div className="row"><span className={`tile-icon pastel ${tone(value, limit)}`}>{icon}</span><div><h2>{label}</h2><p className="muted small">{caption}</p></div></div><Badge tone={tone(value, limit)}>{limit ? `${value}% used` : "Unlimited"}</Badge></div>
    <div className="row" style={{ gap: 18, marginTop: 18 }}>
      <svg width="92" height="92" viewBox="0 0 100 100"><circle cx="50" cy="50" r="42" fill="none" stroke="var(--line)" strokeWidth="9" />{limit && <circle cx="50" cy="50" r="42" fill="none" stroke="var(--brand)" strokeWidth="9" strokeLinecap="round" strokeDasharray={size} strokeDashoffset={size * (1 - value / 100)} transform="rotate(-90 50 50)" />}<text x="50" y="55" textAnchor="middle" style={{ fontSize: 18, fontWeight: 750, fill: "var(--text)" }}>{limit ? `${value}%` : "∞"}</text></svg>
      <div><b style={{ fontSize: 24 }}>{fmt(used)}</b><div className="muted">of {limit ? fmt(limit) : "unlimited"}</div></div>
    </div>
  </Card>;
}

function Limit({ label, used, limit, detail, format = fmt }) {
  const value = ratio(used, limit);
  return <div style={{ padding: "14px 0", borderBottom: "1px solid var(--line)" }}><div className="spread"><div><b>{label}</b><div className="hint">{detail}</div></div><div style={{ textAlign: "right" }}><b>{limit ? `${format(used)} / ${format(limit)}` : "Unlimited"}</b>{limit ? <div className="hint">{value}% used</div> : null}</div></div>{limit ? <div style={{ height: 7, borderRadius: 99, background: "var(--line)", overflow: "hidden", marginTop: 10 }}><div style={{ width: `${value}%`, height: "100%", borderRadius: 99, background: value >= 90 ? "var(--danger)" : value >= 70 ? "var(--warn)" : "var(--brand)" }} /></div> : null}</div>;
}

export default function Usage({ env }) {
  const platform = useApi("/usage", { params: { env }, interval: 30000 });
  const gateway = useApi("/usage", { service: "gateway", interval: 15000 });
  const p = platform.data || {}, g = gateway.data || {}, day = g.requests?.daily || {}, month = g.requests?.monthly || {}, sockets = g.connections || {};
  return <Layout title="Usage & limits" crumbs={["Usage & limits"]}><PageHead title="Usage & limits" description="Live consumption and the optional guardrails configured for this runtime." actions={<span className="live-dot" title="Refreshes automatically" />} />
    <Loading state={{ loading: platform.loading || gateway.loading, error: platform.error || gateway.error, data: true }}>{() => <><div className="grid three" style={{ marginBottom: 14 }}><Gauge label="Today’s requests" icon="↗" used={day.used} limit={day.limit} caption={reset(day.resets_in)} /><Gauge label="Monthly requests" icon="◔" used={month.used} limit={month.limit} caption={reset(month.resets_in)} /><Gauge label="Live connections" icon="◌" used={sockets.active} limit={sockets.limit} caption={sockets.limit ? "Active WebSocket sessions" : "No connection cap"} /></div><div className="grid two"><Card title="Runtime capacity" actions={<Badge tone="blue">Installation</Badge>}><Limit label="Environments" used={p.environments?.used} limit={p.environments?.limit} detail="Includes previews and blueprints." /><Limit label="API keys" used={p.api_keys?.used} limit={p.api_keys?.limit} detail={`Keys in ${env}; revoked keys do not count.`} /><Limit label="Users" used={0} limit={p.users?.limit} detail={`Per ${env}; checked at account creation.`} /><Limit label="Upload size" used={0} limit={p.uploads?.limit} format={(n) => n ? `${Math.round(n / 1024 / 1024)} MB` : "0"} detail="Maximum size for a single object." /></Card><Card title="Designed for low overhead" actions={<Badge tone="green">Efficient</Badge>}><div className="stack" style={{ gap: 16 }}><Note title="Requests are leased" text="Gateway workers reserve small blocks, then serve normal traffic from memory instead of calling Redis for every request." /><Note title="Checks run at the right boundary" text="Uploads stream through their cap; user and key limits run only at creation; connection limits run only when a socket opens." /><Note title="A small, intentional trade-off" text="Usage may include a reserved request block after a worker restart. Tune the reservation size when tighter accounting matters." /></div></Card></div></>}</Loading>
  </Layout>;
}
function Note({ title, text }) { return <div className="row" style={{ alignItems: "flex-start", gap: 10 }}><span className="tile-icon pastel lavender" style={{ width: 30, height: 30 }}>✓</span><div><b>{title}</b><p className="muted" style={{ margin: "3px 0 0" }}>{text}</p></div></div>; }
