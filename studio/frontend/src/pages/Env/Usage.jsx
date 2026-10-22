import { useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Card, Loading, PageHead } from "../../components/ui";
import { envPath, useApi } from "../../lib/api";

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

/** Entitlement label → the plan limit it shows, for matching usage. */
const LIMIT_KEYS = {
  "New users / month": "max_users_per_month", "Active users": "max_active_users", "API keys": "max_api_keys", "Auth providers": "max_auth_providers",
  "Sessions per user": "max_auth_sessions_per_user", "MFA factors / user": "max_mfa_factors_per_user", "Flow runs / month": "max_flow_runs_per_month",
  "Scheduled jobs": "max_scheduled_jobs", "Function invocations / month": "max_function_invocations_per_month", "Function deployments": "max_function_deployments",
  Storage: "max_storage_bytes", Database: "max_database_bytes", "Largest upload": "max_upload_bytes", Backups: "max_backups",
  Environments: "max_environments", "Realtime channels": "max_realtime_channels", "Outbound webhooks": "max_webhooks", "Inbound hooks": "max_inbound_hooks",
};

function Meter({ id, label, used, limit, detail, selected, onSelect }) {
  const total = number(limit);
  const consumed = number(used);
  const percent = total ? Math.min(100, Math.round((consumed / total) * 100)) : 0;
  const state = percent >= 90 ? "danger" : percent >= 70 ? "warn" : "good";
  return <button type="button" className={`usage-meter w-full p-[21px] [border:1px_solid_var(--line)] rounded-sm bg-surface text-text text-left font-inherit cursor-pointer transition-[border-color_.18s_ease,_box-shadow_.18s_ease,_transform_.18s_ease] [&:hover]:[border-color:color-mix(in_srgb,_var(--brand)_48%,_var(--line))] [&:hover]:transform-[none]! [&.selected]:border-brand [&.selected]:shadow-none [&:focus-visible]:[outline:3px_solid_color-mix(in_srgb,_var(--brand)_30%,_transparent)] [&:focus-visible]:outline-offset-[2px] [&_h3]:m-0 [&_h3]:text-heading [&_p]:m-[5px_0_0] [&_p]:text-muted [&_p]:text-xs [&_p]:leading-[1.45] ${selected ? "selected" : ""}`} onClick={() => onSelect(id)} aria-pressed={selected}>
    <div className="usage-meter-top flex justify-between gap-3.5"><div><h3>{label}</h3><p>{detail}</p></div><div className="usage-reading min-w-[75px] text-right [&_strong]:block [&_strong]:text-[20px] [&_strong]:tabular-nums [&_strong]:tracking-[-.03em] [&_span]:text-muted [&_span]:text-xs">{total ? <><strong>{fmt(consumed)}</strong><span>of {fmt(total)}</span></> : <><strong>Unlimited</strong><span>{fmt(consumed)} used</span></>}</div></div>
    {total ? <><div className="usage-track h-2 mt-5 overflow-hidden rounded-full bg-sunken [&_i]:block [&_i]:h-full [&_i]:min-w-0.5 [&_i]:rounded-[inherit] [&_i]:bg-brand [&_i]:transition-[width_.25s_ease] [&_i.warn]:bg-warn [&_i.danger]:bg-danger" aria-label={`${percent}% of ${label} used`}><i className={state} style={{ width: `${percent}%` }} /></div><div className="usage-percent text-muted text-xs mt-[7px] text-right">{percent}% used</div></> : null}
  </button>;
}

function Policy({ label, value, suffix, description }) {
  const enabled = number(value) > 0;
  return <div className="usage-policy [&_small]:m-[5px_0_0] [&_small]:text-muted [&_small]:text-xs [&_small]:leading-[1.45] grid gap-1 p-[4px_22px] [border-right:1px_solid_var(--line)] [&:first-child]:pl-0 [&:last-child]:pr-0 [&:last-child]:border-0 [&_>_span]:text-muted [&_>_span]:text-ui [&_strong]:text-[20px] [&_strong]:tracking-[-.02em] max-wide-tablet:p-[14px_0] max-wide-tablet:border-r-0 max-wide-tablet:[border-bottom:1px_solid_var(--line)] max-wide-tablet:[&:first-child]:pl-0 max-wide-tablet:[&:last-child]:border-b-0"><span>{label}</span><strong>{enabled ? `${fmt(value)}${suffix}` : "Unlimited"}</strong><small>{description}</small></div>;
}

function Entitlement({ label, value, used, format = fmt }) {
  const limited = number(value) > 0;
  const percent = limited && used != null ? Math.min(100, Math.round((number(used) / number(value)) * 100)) : null;
  const state = percent >= 90 ? "danger" : percent >= 70 ? "warn" : "good";
  return <div className="usage-entitlement grid gap-1 min-h-[76px] p-[14px_16px] [border:1px_solid_var(--line)] rounded-sm bg-canvas transition-[border-color_.15s,_transform_.15s] [&:hover]:[border-color:color-mix(in_srgb,_var(--brand)_38%,_var(--line))] [&:hover]:transform-[none]! [&_span]:text-muted [&_span]:text-xs [&_strong]:text-title [&_strong]:tracking-[-.02em]">
    <span>{label}</span>
    <strong>{limited ? (used != null ? <>{format(used)} <small className="muted text-muted">of {format(value)}</small></> : format(value)) : "Unlimited"}</strong>
    {percent != null && <div className="usage-track h-2 mt-5 overflow-hidden rounded-full bg-sunken [&_i]:block [&_i]:h-full [&_i]:min-w-0.5 [&_i]:rounded-[inherit] [&_i]:bg-brand [&_i]:transition-[width_.25s_ease] [&_i.warn]:bg-warn [&_i.danger]:bg-danger" title={`${percent}% used`}><i className={state} style={{ width: `${percent}%` }} /></div>}
  </div>;
}

export default function Usage({ env }) {
  const projectUsage = useApi("/usage", { service: "project", interval: 15000 });
  const planUsage = useApi(env ? envPath(env, "/plan-usage") : null, { interval: 30000 });
  const usedOf = Object.fromEntries((planUsage.data?.data || []).map((row) => [row.limit, row.used]));
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
  ].map(([group, label, value, format]) => ({ group, label, value, format, key: LIMIT_KEYS[label], used: usedOf[LIMIT_KEYS[label]] })), [limits, usedOf]);
  const visibleEntitlements = category === "All" ? entitlements : entitlements.filter((item) => item.group === category);

  return <Layout title="Usage & plan" crumbs={["Usage & plan"]}>
    <PageHead title="Usage & plan" description="Live usage against the limits of your plan." actions={<><span className="live-dot w-2 h-2 rounded-full bg-ok shadow-none [&.busy]:bg-brand [&.busy]:animate-[blink_0.9s_ease-in-out_infinite] [&.off]:bg-[var(--faint)] [&.off]:shadow-none" title="Refreshes every 15 seconds" /><button type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text sm" onClick={projectUsage.reload}>Refresh</button></>} />
    <Loading state={{ loading: projectUsage.loading, error: projectUsage.error, data: true }}>{() => <div className="usage-page grid gap-5">
      <section className="usage-hero flex items-start justify-between gap-5 p-[26px_28px] rounded-sm bg-brand text-on-ink [&_.eyebrow]:block [&_.eyebrow]:mb-[7px] [&_.eyebrow]:text-2xs [&_.eyebrow]:font-extrabold [&_.eyebrow]:tracking-[.11em] [&_.eyebrow]:text-on-ink/70 [&_h2]:m-0 [&_h2]:text-[27px] [&_h2]:tracking-[-.035em] [&_p]:max-w-[580px] [&_p]:m-[7px_0_0] [&_p]:text-on-ink/80 [&_.badge]:bg-surface [&_.badge]:text-text max-wide-tablet:p-[22px]">
        <div><span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">PROJECT PLAN</span><h2>Usage at a glance</h2><p>Your requests and connections are measured across every environment in this project.</p></div>
        <Badge tone="brand">Live</Badge>
      </section>
      <div className="usage-grid grid grid-cols-3 gap-4 max-wide-tablet:grid-cols-1">
        {Object.entries(metrics).map(([id, metric]) => <Meter key={id} id={id} {...metric} selected={activeMetric === id} onSelect={setActiveMetric} />)}
      </div>
      <section className="usage-detail flex items-center justify-between gap-6 p-[19px_22px] [border:1px_solid_color-mix(in_srgb,_var(--brand)_18%,_var(--line))] rounded-sm bg-[color-mix(in_srgb,_var(--brand)_6%,_var(--surface))] [&_.eyebrow]:block [&_.eyebrow]:mb-1 [&_.eyebrow]:text-brand-strong [&_.eyebrow]:text-3xs [&_.eyebrow]:font-extrabold [&_.eyebrow]:tracking-[.11em] [&_h3]:m-0 [&_h3]:text-heading [&_p]:max-w-[650px] [&_p]:m-[4px_0_0] [&_p]:text-muted [&_p]:text-ui [&_p]:leading-[1.45] max-wide-tablet:items-start max-wide-tablet:flex-col" aria-live="polite">
        <div><span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">FOCUS</span><h3>{active.label}</h3><p>{active.context}</p></div>
        <div className="usage-detail-reading flex-none text-right [&_strong]:block [&_strong]:text-[19px] [&_strong]:tabular-nums [&_span]:text-muted [&_span]:text-xs max-wide-tablet:text-left"><strong>{number(active.limit) ? `${fmt(active.used)} / ${fmt(active.limit)}` : "Unlimited"}</strong><span>{number(active.limit) ? `${Math.min(100, Math.round((number(active.used) / number(active.limit)) * 100))}% used` : `${fmt(active.used)} used`}</span></div>
      </section>
      <Card icon="policies" title="Request policy" className="usage-policy-card m-0">
        <div className="usage-policy-grid grid grid-cols-3 gap-0 max-wide-tablet:grid-cols-1">
          <Policy label="Rate limit" value={limits.rate_limit} suffix={` / ${window}s`} description="Per API key or client address." />
          <Policy label="Daily allowance" value={limits.daily_request_limit} suffix=" requests" description="Resets at the next UTC day." />
          <Policy label="Monthly allowance" value={limits.monthly_request_limit} suffix=" requests" description="Resets at the next UTC month." />
        </div>
        <p className="usage-note m-[20px_0_0] pt-4 [border-top:1px_solid_var(--line)] text-muted text-ui">Plan limits are set by Pawabase Cloud. Contact your organization administrator to change a plan.</p>
      </Card>
      <Card icon="usage" title="Plan entitlements" className="usage-policy-card m-0" actions={<span className="usage-entitlement-count text-muted text-xs">{visibleEntitlements.length} shown</span>}>
        <div className="usage-filters [&_button:focus-visible]:[outline:3px_solid_color-mix(in_srgb,_var(--brand)_30%,_transparent)] [&_button:focus-visible]:outline-offset-[2px] flex flex-wrap gap-2 m-[2px_0_18px] [&_button]:p-[7px_12px] [&_button]:[border:1px_solid_var(--line)] [&_button]:rounded-sm [&_button]:bg-surface [&_button]:text-muted [&_button]:[font:600_12px_inherit] [&_button]:cursor-pointer [&_button]:transition-[background_.15s,_border-color_.15s,_color_.15s] [&_button:hover]:border-brand [&_button:hover]:text-brand-strong [&_button.active]:border-brand [&_button.active]:bg-brand-soft [&_button.active]:text-brand-strong" role="tablist" aria-label="Entitlement category">
          {["All", "Access", "Automation", "Data", "Infrastructure"].map((item) => <button key={item} type="button" role="tab" aria-selected={category === item} className={category === item ? "active" : ""} onClick={() => setCategory(item)}>{item}</button>)}
        </div>
        <div className="usage-entitlement-grid grid grid-cols-3 gap-2.5 max-wide-tablet:grid-cols-1">
          {visibleEntitlements.map((item) => <Entitlement key={item.label} label={item.label} value={item.value} used={item.used} format={item.format} />)}
        </div>
      </Card>
    </div>}</Loading>
  </Layout>;
}
