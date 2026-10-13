import { useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Card, Loading, PageHead } from "../../components/ui";
import { useApi } from "../../lib/api";

const number = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
const fmt = (value) => number(value).toLocaleString();
const bytes = (value) => {
  const size = number(value);
  if (!size) return "Unlimited";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  return `${(size / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
};
const reset = (seconds) => {
  const value = number(seconds);
  if (!value) return "Usage refreshes automatically";
  return value >= 86400 ? `Resets in ${Math.ceil(value / 86400)} days` : `Resets in ${Math.ceil(value / 3600)} hours`;
};

function Meter({ id, label, used, limit, detail, selected, onSelect }) {
  const total = number(limit);
  const consumed = number(used);
  const percent = total ? Math.min(100, Math.round((consumed / total) * 100)) : 0;
  const state = percent >= 90 ? "danger" : percent >= 70 ? "warn" : "good";
  return <button type="button" className={`usage-meter ${selected ? "selected" : ""}`} onClick={() => onSelect(id)} aria-pressed={selected}>
    <div className="usage-meter-top"><div><h3>{label}</h3><p>{detail}</p></div><div className="usage-reading">{total ? <><strong>{fmt(consumed)}</strong><span>of {fmt(total)}</span></> : <><strong>Unlimited</strong><span>{fmt(consumed)} used</span></>}</div></div>
    {total ? <><div className="usage-track" aria-label={`${percent}% of ${label} used`}><i className={state} style={{ width: `${percent}%` }} /></div><div className="usage-percent">{percent}% used</div></> : null}
  </button>;
}

function Policy({ label, value, suffix, description }) {
  const enabled = number(value) > 0;
  return <div className="usage-policy"><span>{label}</span><strong>{enabled ? `${fmt(value)}${suffix}` : "Unlimited"}</strong><small>{description}</small></div>;
}

function Entitlement({ label, value, format = fmt }) {
  return <div className="usage-entitlement"><span>{label}</span><strong>{number(value) ? format(value) : "Unlimited"}</strong></div>;
}

export default function Usage() {
  const projectUsage = useApi("/usage", { service: "project", interval: 15000 });
  const [activeMetric, setActiveMetric] = useState("day");
  const [category, setCategory] = useState("All");
  const project = projectUsage.data?.project || {};
  const usage = project.usage || {};
  const limits = project.limits || {};
  const day = usage.requests?.daily || {};
  const month = usage.requests?.monthly || {};
  const sockets = usage.connections || {};
  const window = number(limits.rate_window) || 60;
  const metrics = {
    day: { label: "Requests today", used: day.used, limit: limits.daily_request_limit, detail: reset(day.resets_in), context: "Daily requests are shared by all environments in this project and reset at the next UTC day." },
    month: { label: "Requests this month", used: month.used, limit: limits.monthly_request_limit, detail: reset(month.resets_in), context: "Monthly requests are shared by all environments in this project and reset at the next UTC month." },
    connections: { label: "Realtime connections", used: sockets.active, limit: limits.max_active_connections, detail: "Active WebSocket connections in this runtime.", context: "Connections show the live total across this project. Closing an idle client frees a connection immediately." },
  };
  const active = metrics[activeMetric];
  const entitlements = useMemo(() => [
    ["Access", "New users / month", limits.max_users_per_month], ["Access", "Active users", limits.max_active_users],
    ["Access", "API keys", limits.max_api_keys], ["Access", "Auth providers", limits.max_auth_providers],
    ["Access", "Sessions per user", limits.max_auth_sessions_per_user], ["Access", "MFA factors / user", limits.max_mfa_factors_per_user],
    ["Automation", "Flow runs / month", limits.max_flow_runs_per_month], ["Automation", "Scheduled jobs", limits.max_scheduled_jobs],
    ["Automation", "Function invocations / month", limits.max_function_invocations_per_month], ["Automation", "Function deployments", limits.max_function_deployments],
    ["Data", "Storage", limits.max_storage_bytes, bytes], ["Data", "Database", limits.max_database_bytes, bytes],
    ["Data", "Largest upload", limits.max_upload_bytes, bytes], ["Data", "Backups", limits.max_backups],
    ["Infrastructure", "Environments", limits.max_environments], ["Infrastructure", "Realtime channels", limits.max_realtime_channels],
    ["Infrastructure", "Outbound webhooks", limits.max_webhooks], ["Infrastructure", "Inbound hooks", limits.max_inbound_hooks],
  ].map(([group, label, value, format]) => ({ group, label, value, format })), [limits]);
  const visibleEntitlements = category === "All" ? entitlements : entitlements.filter((item) => item.group === category);

  return <Layout title="Usage & limits" crumbs={["Usage & limits"]}>
    <PageHead title="Usage & limits" description="Live usage and plan limits for this project." actions={<><span className="live-dot" title="Refreshes every 15 seconds" /><button type="button" className="btn sm" onClick={projectUsage.reload}>Refresh</button></>} />
    <Loading state={{ loading: projectUsage.loading, error: projectUsage.error, data: true }}>{() => <div className="usage-page">
      <section className="usage-hero">
        <div><span className="eyebrow">PROJECT PLAN</span><h2>Usage at a glance</h2><p>Your requests and connections are measured across every environment in this project.</p></div>
        <Badge tone="brand">Live</Badge>
      </section>
      <div className="usage-grid">
        {Object.entries(metrics).map(([id, metric]) => <Meter key={id} id={id} {...metric} selected={activeMetric === id} onSelect={setActiveMetric} />)}
      </div>
      <section className="usage-detail" aria-live="polite">
        <div><span className="eyebrow">FOCUS</span><h3>{active.label}</h3><p>{active.context}</p></div>
        <div className="usage-detail-reading"><strong>{number(active.limit) ? `${fmt(active.used)} / ${fmt(active.limit)}` : "Unlimited"}</strong><span>{number(active.limit) ? `${Math.min(100, Math.round((number(active.used) / number(active.limit)) * 100))}% used` : `${fmt(active.used)} used`}</span></div>
      </section>
      <Card title="Request policy" className="usage-policy-card">
        <div className="usage-policy-grid">
          <Policy label="Rate limit" value={limits.rate_limit} suffix={` / ${window}s`} description="Per API key or client address." />
          <Policy label="Daily allowance" value={limits.daily_request_limit} suffix=" requests" description="Resets at the next UTC day." />
          <Policy label="Monthly allowance" value={limits.monthly_request_limit} suffix=" requests" description="Resets at the next UTC month." />
        </div>
        <p className="usage-note">Plan limits are set by Pawabase Cloud. Contact your organization administrator to change a plan.</p>
      </Card>
      <Card title="Plan entitlements" className="usage-policy-card" actions={<span className="usage-entitlement-count">{visibleEntitlements.length} shown</span>}>
        <div className="usage-filters" role="tablist" aria-label="Entitlement category">
          {["All", "Access", "Automation", "Data", "Infrastructure"].map((item) => <button key={item} type="button" role="tab" aria-selected={category === item} className={category === item ? "active" : ""} onClick={() => setCategory(item)}>{item}</button>)}
        </div>
        <div className="usage-entitlement-grid">
          {visibleEntitlements.map((item) => <Entitlement key={item.label} label={item.label} value={item.value} format={item.format} />)}
        </div>
      </Card>
    </div>}</Loading>
  </Layout>;
}
