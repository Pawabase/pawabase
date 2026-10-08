import { useEffect, useMemo, useState } from "react";
import Chart from "../../../components/Chart";
import { Card, Empty, Loading, when } from "../../../components/ui";
import { envPath, get } from "../../../lib/api";
import JobDetail from "./JobDetail";
import { Kpi } from "./kit";
import { ms } from "./shared";
import { AREA_SECTION, COLOR, Legend, num, pct } from "./system";

// The same probe the header light uses: Studio asks every service for /health.
function useServices() {
  const [services, setServices] = useState(null);
  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const response = await fetch("/studio/status", { credentials: "same-origin", headers: { Accept: "application/json" } });
        const body = await response.json();
        if (alive) setServices(body.services || []);
      } catch { if (alive) setServices([]); }
    };
    check();
    const timer = setInterval(check, 30000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  return services;
}

const sum = (keys) => (row) => keys.reduce((n, k) => n + (row[k] || 0), 0);

/** The six headline signals. Selecting one swaps the big chart underneath. */
function indicators(s) {
  const t = s.traffic.totals;
  return [
    { key: "requests", label: "Requests", value: num(t.requests), sub: `${t.error_rate}% server errors`, bad: t.error_rate >= 5, rows: s.traffic.series, spark: sum(["requests"]), color: COLOR.info, section: "routes",
      lines: [["requests", "Requests", COLOR.info, "area"], ["client_errors", "4xx", COLOR.warn, "line"], ["errors", "5xx", COLOR.bad, "line"]] },
    { key: "latency", label: "Latency", value: ms(t.avg_latency_ms), sub: "average per request", rows: s.traffic.series, spark: (r) => r.latency_ms || 0, color: COLOR.brand, section: "routes",
      lines: [["latency_ms", "Average latency (ms)", COLOR.brand, "area"]] },
    { key: "jobs", label: "Jobs", value: num(s.jobs.totals.total), sub: s.jobs.totals.failed ? `${num(s.jobs.totals.failed)} failed · ${s.jobs.backlog.waiting} waiting` : `${s.jobs.backlog.waiting} waiting`, bad: s.jobs.totals.failed > 0, rows: s.jobs.series, spark: sum(["succeeded", "failed", "retrying"]), color: COLOR.ok, section: "queues",
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
    ...s.jobs.recent_failures.map((x) => ({ kind: "job", count: x.count, id: x.id, title: x.job, detail: `${x.queue} · ${x.error}`, at: x.at, section: "queues" })),
    ...s.flows.recent_failures.map((x) => ({ kind: "flow", count: x.count, id: x.id, title: x.flow, detail: x.error, at: x.at, section: "flows" })),
    ...s.webhooks.recent_failures.map((x) => ({ kind: "webhook", count: x.count, id: x.id, title: `${x.endpoint} · ${x.event}`, detail: `${x.status_code ? `HTTP ${x.status_code} · ` : ""}${x.error}`, at: x.at, section: "webhooks" })),
    ...s.mail.recent_failures.map((x) => ({ kind: "mail", count: x.count, id: x.id, title: x.subject || "(no subject)", detail: x.error, at: x.at, section: "mail" })),
  ].sort((a, b) => new Date(b.at) - new Date(a.at));
}

export default function Overview({ env, system, onOpen }) {
  const services = useServices();
  const [focus, setFocus] = useState("requests");
  const [filter, setFilter] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const [job, setJob] = useState(null);
  const base = envPath(env);
  const openIncident = async (item) => {
    if (item.kind === "job") setJob(await get(`${base}/jobs/${item.id}`));
    else onOpen(item.section);
  };
  return (
    <Loading state={system}>
      {(s) => <Body s={s} {...{ services, focus, setFocus, filter, setFilter, showAll, setShowAll, onOpen, openIncident }} job={job} closeJob={() => setJob(null)} base={base} />}
    </Loading>
  );
}

function Body({ s, services, focus, setFocus, filter, setFilter, showAll, setShowAll, onOpen, openIncident, job, closeJob, base }) {
  const items = useMemo(() => indicators(s), [s]);
  const active = items.find((i) => i.key === focus) || items[0];
  const feed = useMemo(() => incidents(s), [s]);
  const shown = feed.filter((i) => filter === "all" || i.kind === filter);
  const critical = s.problems.filter((p) => p.severity === "critical").length;
  const warnings = s.problems.length - critical;
  const down = (services || []).filter((x) => x.status === "down").length;
  const tone = critical || down ? "error" : warnings ? "warn" : "ok";
  const headline = critical || warnings
    ? [critical && `${critical} critical`, warnings && `${warnings} warning${warnings > 1 ? "s" : ""}`].filter(Boolean).join(" · ")
    : "Everything looks healthy";
  const findings = showAll ? s.problems : s.problems.slice(0, 4);
  return (
    <div className="stack lg">
      <div className={`banner ${tone}`}>
        <b>{down ? `${down} service${down > 1 ? "s" : ""} unreachable · ` : ""}{headline}</b>
        <span className="services">
          {(services || []).map((x) => <span key={x.name} title={x.detail || ""}><i className={`light ${x.status}`} />{x.name}{x.status === "up" && x.latency_ms != null ? <span className="muted"> {Math.round(x.latency_ms)}ms</span> : null}</span>)}
        </span>
      </div>

      <div className="kpis">
        {items.map((i) => (
          <Kpi key={i.key} label={i.label} value={i.value} sub={i.sub} bad={i.bad} values={i.rows.map(i.spark)} color={i.bad ? COLOR.bad : i.color} active={i.key === active.key} onClick={() => setFocus(i.key)} />
        ))}
      </div>

      <Card title={active.label} className="chart-card"
        actions={<><Legend items={active.lines.map(([, label, color]) => [label, color])} /><button type="button" className="chip" onClick={() => onOpen(active.section)}>Open {active.label.toLowerCase()} →</button></>}>
        <Chart height={230} stepSeconds={s.window.step_seconds} data={active.rows}
          series={active.lines.map(([key, label, color, type]) => ({ key, label, color, type }))} empty={`No ${active.label.toLowerCase()} activity in this window.`} />
      </Card>

      <div className="obs-split">
        <Card title="Recent failures" actions={<div className="chips">{FILTERS.map(([key, label]) => <button key={key} type="button" className={`chip ${filter === key ? "active" : ""}`} onClick={() => setFilter(key)}>{label}</button>)}</div>}>
          {shown.length === 0 ? <Empty>Nothing failed{filter === "all" ? "" : ` in ${KIND[filter].toLowerCase()}s`} in this window.</Empty> : shown.slice(0, 12).map((i) => (
            <div key={`${i.kind}${i.id}`} className="feed-item" onClick={() => openIncident(i)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && openIncident(i)}>
              <span className="kind">{KIND[i.kind]}</span>
              <span className="body"><b>{i.title}{i.count > 1 && <span className="count">×{i.count}</span>}</b><span title={i.detail}>{i.detail}</span></span>
              <time title={i.at}>{when(i.at)}</time>
            </div>
          ))}
        </Card>
        <Card title="Needs attention" actions={<span className="muted small">{s.problems.length ? `${s.problems.length} finding${s.problems.length > 1 ? "s" : ""}` : "all clear"}</span>}>
          {s.problems.length === 0 ? <Empty>No findings in this window.</Empty> : <>
            {findings.map((p, i) => (
              <button key={i} type="button" className="finding" onClick={() => onOpen(AREA_SECTION[p.area])}>
                <i className={`dot ${p.severity === "critical" ? "bad" : "warn"}`} />
                <span><small>{p.area}</small>{p.message}</span>
              </button>
            ))}
            {s.problems.length > 4 && <button type="button" className="chip" style={{ marginTop: 8 }} onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${s.problems.length}`}</button>}
          </>}
        </Card>
      </div>
      {job && <JobDetail base={base} job={job} onClose={closeJob} />}
    </div>
  );
}
