import { useEffect, useMemo, useState } from "react";
import Chart from "../../../components/Chart";
import { Card, Empty, Loading, Segmented, when } from "../../../components/ui";
import { envPath, get, useApi } from "../../../lib/api";
import JobDetail from "./JobDetail";
import { ms } from "./shared";
import { AREA_LABEL, COLOR, Legend, actionable, num } from "./system";

const sum = (keys) => (row) => keys.reduce((n, k) => n + (row[k] || 0), 0);

/** The six headline signals. Selecting one swaps the big chart underneath. */
function indicators(s) {
  const t = s.traffic.totals;
  return [
    { key: "requests", label: "Requests", value: num(t.requests), sub: `${t.error_rate}% server errors`, bad: t.error_rate >= 5, rows: s.traffic.series, spark: sum(["requests"]), color: COLOR.info, section: "routes",
      lines: [["requests", "Requests", COLOR.info, "area"], ["client_errors", "4xx", COLOR.warn, "line"], ["errors", "5xx", COLOR.bad, "line"]] },
    { key: "latency", label: "Latency", value: ms(t.avg_latency_ms), sub: "average per request", rows: s.traffic.series, spark: (r) => r.latency_ms || 0, color: COLOR.brand, section: "routes", format: ms,
      lines: [["latency_ms", "Average latency (ms)", COLOR.brand, "bar"]] },
    { key: "jobs", label: "Jobs", value: num(s.jobs.totals.total), sub: s.jobs.totals.failed ? `${num(s.jobs.totals.failed)} failed · ${s.jobs.backlog.waiting} waiting` : `${s.jobs.backlog.waiting} waiting`, bad: s.jobs.totals.failed > 0, rows: s.jobs.series, spark: sum(["succeeded", "failed", "retrying"]), color: COLOR.ok, section: "jobs",
      lines: [["succeeded", "Succeeded", COLOR.ok, "area"], ["failed", "Failed", COLOR.bad, "line"], ["retrying", "Retrying", COLOR.warn, "line"]] },
    { key: "flows", label: "Flow runs", value: num(s.flows.totals.runs), sub: s.flows.totals.failed ? `${s.flows.totals.failed} failed` : "none failed", bad: s.flows.totals.failed > 0, rows: s.flows.series, spark: sum(["succeeded", "failed"]), color: COLOR.brand, section: "flows",
      lines: [["succeeded", "Succeeded", COLOR.ok, "area"], ["failed", "Failed", COLOR.bad, "line"]] },
    { key: "webhooks", label: "Webhooks", value: num(s.webhooks.totals.total), sub: s.webhooks.totals.failed ? `${s.webhooks.totals.failed} failed` : "none failed", bad: s.webhooks.totals.failed > 0, rows: s.webhooks.series, spark: sum(["delivered", "failed", "pending"]), color: COLOR.info, section: "webhooks",
      lines: [["delivered", "Delivered", COLOR.ok, "area"], ["failed", "Failed", COLOR.bad, "line"], ["pending", "Pending", COLOR.warn, "line"]] },
    { key: "mail", label: "Mail", value: num(s.mail.totals.total), sub: s.mail.totals.failed ? `${s.mail.totals.failed} failed` : `${s.mail.totals.suppressed} suppressed`, bad: s.mail.totals.failed > 0, rows: s.mail.series, spark: sum(["sent", "suppressed", "failed"]), color: COLOR.warn, section: "mail",
      lines: [["sent", "Sent", COLOR.ok, "area"], ["suppressed", "Suppressed", COLOR.warn, "line"], ["failed", "Failed", COLOR.bad, "line"]] },
  ];
}

const KIND = { job: "Job", flow: "Flow", webhook: "Webhook", mail: "Mail" };
const FILTERS = [["all", "All"], ["job", "Jobs"], ["flow", "Flows"], ["webhook", "Webhooks"], ["mail", "Mail"]];

/** Every recent failure from every subsystem, newest first. */
function incidents(s) {
  return [
    ...s.jobs.recent_failures.map((x) => ({ kind: "job", count: x.count, id: x.id, title: x.job, detail: x.error, at: x.at, section: "jobs" })),
    ...s.flows.recent_failures.map((x) => ({ kind: "flow", count: x.count, id: x.id, title: x.flow, detail: x.error, at: x.at, section: "flows" })),
    ...s.webhooks.recent_failures.map((x) => ({ kind: "webhook", count: x.count, id: x.id, title: `${x.endpoint} · ${x.event}`, detail: `${x.status_code ? `HTTP ${x.status_code} · ` : ""}${x.error}`, at: x.at, section: "webhooks" })),
    ...s.mail.recent_failures.map((x) => ({ kind: "mail", count: x.count, id: x.id, title: x.subject || "(no subject)", detail: x.error, at: x.at, section: "mail" })),
  ].sort((a, b) => new Date(b.at) - new Date(a.at));
}

export default function Overview({ env, system, onOpen }) {
  // Definitions that cannot run (a flow that points at a missing function, say) are findings too.
  const definitions = useApi(`${envPath(env)}/overview`);
  const [focus, setFocus] = useState("requests");
  const [filter, setFilter] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const [job, setJob] = useState(null);
  const base = envPath(env);
  const openIncident = async (item) => {
    if (item.kind === "job") setJob(await get(`${base}/jobs/${item.id}`));
    else onOpen(item.section);
  };
  const found = (definitions.data?.problems || []).slice(0, 6).map((message) => ({ severity: "warning", area: "definitions", message: typeof message === "string" ? message : message.message || JSON.stringify(message) }));
  return (
    <Loading state={system}>
      {(report) => (
        <Body
          s={{ ...report, problems: [...found, ...report.problems] }}
          {...{ focus, setFocus, filter, setFilter, showAll, setShowAll, onOpen, openIncident }} job={job} closeJob={() => setJob(null)} base={base}
        />
      )}
    </Loading>
  );
}

function Body({ s: report, focus, setFocus, filter, setFilter, showAll, setShowAll, onOpen, openIncident, job, closeJob, base }) {
  const s = useMemo(() => ({ ...report, problems: actionable(report.problems) }), [report]);
  const items = useMemo(() => indicators(s), [s]);
  const active = items.find((i) => i.key === focus) || items[0];
  const feed = useMemo(() => incidents(s), [s]);
  const shown = feed.filter((i) => filter === "all" || i.kind === filter);
  const critical = s.problems.filter((p) => p.severity === "critical").length;
  const warnings = s.problems.length - critical;
  const tone = critical ? "error" : warnings ? "warn" : "ok";
  const headline = critical || warnings
    ? [critical && `${critical} critical`, warnings && `${warnings} warning${warnings > 1 ? "s" : ""}`].filter(Boolean).join(" · ")
    : "Everything looks healthy";
  const findings = showAll ? s.problems : s.problems.slice(0, 4);
  return (
    <div className="flex flex-col gap-3">
      <div className={`banner flex items-center justify-between gap-3.5 p-[11px_16px] rounded-sm text-body flex-wrap [&.ok]:bg-ok-soft [&.ok]:text-ok [&.warn]:bg-warn-soft [&.warn]:text-warn [&.error]:bg-danger-soft [&.error]:text-danger [&_.services]:flex [&_.services]:gap-3.5 [&_.services]:flex-wrap [&_.services]:text-text [&_.services]:text-ui-sm [&_.services_span]:inline-flex [&_.services_span]:items-center [&_.services_span]:gap-1.5 ${tone}`}>
        <b>{headline}</b>
      </div>

      <Card icon="usage" title={<Segmented options={items.map((i) => [i.key, i.label])} value={active.key} onChange={setFocus} />} className="chart-card [&_h2]:text-heading"
        actions={<><Legend items={active.lines.map(([, label, color]) => [label, color])} /><button type="button" className="chip [border:1px_solid_var(--line)] bg-none text-muted rounded-xs p-[3px_11px] font-inherit text-xs cursor-pointer [&:hover]:border-brand [&:hover]:text-brand-strong [&.on]:border-solid [&.on]:border-brand [&.on]:bg-brand-soft [&.on]:text-brand-strong font-semibold [&.active]:bg-text [&.active]:text-surface [&.active]:border-text" onClick={() => onOpen(active.section)}>Open {active.label.toLowerCase()} →</button></>}>
        <Chart height={230} stepSeconds={s.window.step_seconds} data={active.rows} format={active.format}
          series={active.lines.map(([key, label, color, type]) => ({ key, label, color, type }))} empty={`No ${active.label.toLowerCase()} activity in this window.`} />
      </Card>

      <div className="obs-split grid grid-cols-[minmax(0,_1.5fr)_minmax(0,_1fr)] gap-3.5 items-start max-layout:grid-cols-1">
        <Card icon="pulse" title="Recent failures" actions={<div className="chips flex flex-wrap gap-1.5">{FILTERS.map(([key, label]) => <button key={key} type="button" className={`chip [border:1px_solid_var(--line)] bg-none text-muted rounded-xs p-[3px_11px] font-inherit text-xs cursor-pointer [&:hover]:border-brand [&:hover]:text-brand-strong [&.on]:border-solid [&.on]:border-brand [&.on]:bg-brand-soft [&.on]:text-brand-strong font-semibold [&.active]:bg-text [&.active]:text-surface [&.active]:border-text ${filter === key ? "active" : ""}`} onClick={() => setFilter(key)}>{label}</button>)}</div>}>
          {shown.length === 0 ? <Empty>Nothing failed{filter === "all" ? "" : ` in ${KIND[filter].toLowerCase()}s`} in this window.</Empty> : shown.slice(0, 12).map((i) => (
            <div key={`${i.kind}${i.id}`} className="feed-item flex gap-3 items-start p-[11px_6px] [border-bottom:1px_solid_var(--line)] cursor-pointer rounded-none [&:last-child]:border-b-0 [&:hover]:bg-sunken [&_.kind]:flex-none [&_.kind]:w-[74px] [&_.kind]:text-2xs [&_.kind]:font-bold [&_.kind]:uppercase [&_.kind]:tracking-[0.04em] [&_.kind]:text-muted [&_.kind]:pt-0.5 [&_.body]:min-w-0 [&_.body]:flex-1 [&_.body_b]:block [&_.body_b]:text-body [&_.body_>_span]:block [&_.body_>_span]:text-ui-sm [&_.body_>_span]:text-muted [&_.body_>_span]:overflow-hidden [&_.body_>_span]:text-ellipsis [&_.body_>_span]:whitespace-nowrap [&_time]:flex-none [&_time]:text-xs [&_time]:text-faint [&_.count]:inline-block [&_.count]:ml-2 [&_.count]:[vertical-align:1px] [&_.count]:text-2xs [&_.count]:font-bold [&_.count]:p-[1px_7px] [&_.count]:rounded-sm [&_.count]:bg-danger-soft [&_.count]:text-danger" onClick={() => openIncident(i)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openIncident(i)}>
              <span className="kind">{KIND[i.kind]}</span>
              <span className="body"><b>{i.title}{i.count > 1 && <span className="count">×{i.count}</span>}</b><span title={i.detail}>{i.detail}</span></span>
              <time title={i.at}>{when(i.at)}</time>
            </div>
          ))}
        </Card>
        <Card icon="pulse" title="Needs attention" actions={<span className="muted text-muted small text-ui-sm">{s.problems.length ? `${s.problems.length} finding${s.problems.length > 1 ? "s" : ""}` : "all clear"}</span>}>
          {s.problems.length === 0 ? <Empty>No findings in this window.</Empty> : <>
            {findings.map((p, i) => (
              <button key={i} type="button" className="finding flex gap-2.5 items-start w-full text-left border-0 bg-none font-inherit text-inherit p-[9px_6px] rounded-sm cursor-pointer [&:hover]:bg-sunken [&_.dot]:mt-1.5 [&_small]:block [&_small]:text-muted [&_small]:text-caption [&_small]:capitalize" onClick={() => onOpen(AREA_SECTION[p.area])}>
                <i className={`dot w-[7px] h-[7px] rounded-full bg-[var(--faint)] flex-none inline-block [&.ok]:bg-ok [&.warn]:bg-warn [&.bad]:bg-danger [&.info]:bg-info ${p.severity === "critical" ? "bad" : "warn"}`} />
                <span><small>{AREA_LABEL[p.area] || p.area}</small>{p.message}</span>
              </button>
            ))}
            {s.problems.length > 4 && <button type="button" className="chip [border:1px_solid_var(--line)] bg-none text-muted rounded-xs p-[3px_11px] font-inherit text-xs cursor-pointer [&:hover]:border-brand [&:hover]:text-brand-strong [&.on]:border-solid [&.on]:border-brand [&.on]:bg-brand-soft [&.on]:text-brand-strong font-semibold [&.active]:bg-text [&.active]:text-surface [&.active]:border-text" style={{ marginTop: 8 }} onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${s.problems.length}`}</button>}
          </>}
        </Card>
      </div>
      {job && <JobDetail base={base} job={job} onClose={closeJob} />}
    </div>
  );
}
