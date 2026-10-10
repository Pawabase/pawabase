import Layout from "../../components/Layout";
import { Badge, Card, Loading, PageHead } from "../../components/ui";
import { useApi } from "../../lib/api";

const fmt = (n) => Number(n || 0).toLocaleString();
const reset = (s) => !s ? "—" : s >= 86400 ? `resets in ${Math.ceil(s / 86400)}d` : `resets in ${Math.ceil(s / 3600)}h`;
const ratio = (used, limit) => limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
const tone = (value, limit) => !limit ? "blue" : value >= 90 ? "red" : value >= 70 ? "yellow" : "green";

function Limit({ label, used, limit, detail, format = fmt }) {
  const value = ratio(used, limit);
  return <div style={{ padding: "14px 0", borderBottom: "1px solid var(--line)" }}><div className="spread"><div><b>{label}</b><div className="hint">{detail}</div></div><div style={{ textAlign: "right" }}><b>{limit ? `${format(used)} / ${format(limit)}` : "Unlimited"}</b>{limit ? <div className="hint">{value}% used</div> : null}</div></div>{limit ? <div style={{ height: 7, borderRadius: 99, background: "var(--line)", overflow: "hidden", marginTop: 10 }}><div style={{ width: `${value}%`, height: "100%", borderRadius: 99, background: value >= 90 ? "var(--danger)" : value >= 70 ? "var(--warn)" : "var(--brand)" }} /></div> : null}</div>;
}

export default function Usage({ env }) {
  const platform = useApi("/usage", { params: { env }, interval: 30000 });
  const gateway = useApi("/usage", { service: "gateway", interval: 15000 });
  const p = platform.data || {}, g = gateway.data || {}, day = g.requests?.daily || {}, month = g.requests?.monthly || {}, sockets = g.connections || {};
  return <Layout title="Usage & limits" crumbs={["Usage & limits"]}><PageHead title="Usage & limits" description="Live consumption and the optional guardrails configured for this runtime." actions={<span className="live-dot" title="Refreshes automatically" />} />
    <Loading state={{ loading: platform.loading || gateway.loading, error: platform.error || gateway.error, data: true }}>{() => <><Card title="Consumption" style={{ marginBottom: 14 }}><Limit label="Today’s requests" used={day.used} limit={day.limit} detail={reset(day.resets_in)} /><Limit label="Monthly requests" used={month.used} limit={month.limit} detail={reset(month.resets_in)} /><Limit label="Live connections" used={sockets.active} limit={sockets.limit} detail={sockets.limit ? "Active WebSocket sessions" : "No connection cap"} /></Card><Card title="Runtime capacity" actions={<Badge tone="blue">Installation</Badge>}><Limit label="Environments" used={p.environments?.used} limit={p.environments?.limit} detail="Includes previews and blueprints." /><Limit label="API keys" used={p.api_keys?.used} limit={p.api_keys?.limit} detail={`Keys in ${env}; revoked keys do not count.`} /><Limit label="Users" used={0} limit={p.users?.limit} detail={`Per ${env}; checked at account creation.`} /><Limit label="Upload size" used={0} limit={p.uploads?.limit} format={(n) => n ? `${Math.round(n / 1024 / 1024)} MB` : "0"} detail="Maximum size for a single object." /></Card></>}</Loading>
  </Layout>;
}
