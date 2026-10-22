import { useEffect, useMemo, useState } from "react";
import { router } from "@inertiajs/react";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Button, PageHead, Segmented } from "../../components/ui";
import { envPath, get } from "../../lib/api";
import AuditLog from "./observability/Audit";
import Errors from "./observability/Errors";
import { Alarms, Logs, Metrics } from "./observability/Explore";
import Flows from "./observability/Flows";
import { Events, Mail, Webhooks } from "./observability/Messaging";
import Overview from "./observability/Overview";
import JobsPanel from "./observability/JobsPanel";
import { EmitEvent, EventLog, EventMap, FlowRuns } from "./observability/EventTools";
import RealtimeHealth from "./observability/RealtimeHealth";
import Requests from "./observability/Requests";
import RequestTrace from "./observability/RequestTrace";
import RouteDetail from "./observability/RouteDetail";
import Routes from "./observability/Routes";
import { AREA_SECTION, useSystem } from "./observability/system";

const WINDOWS = [[15, "15m"], [60, "1h"], [360, "6h"], [1440, "24h"], [10080, "7d"], [20160, "14d"]];

// The sidebar answers "what do you want to look at"; each entry is one page of the same place.
const TABS = [
  ["overview", "Overview", [["overview", "Overview"]]],
  ["requests", "Requests", [["routes", "Routes"], ["errors", "Errors"], ["requests", "Requests"]]],
  ["background", "Background work", [["jobs", "Jobs"], ["flows", "Flow runs"]]],
  ["messaging", "Events and messages", [["events", "Events"], ["event-map", "Event map"], ["webhooks", "Webhooks"], ["mail", "Mail"], ["realtime", "Realtime"]]],
  ["explore", "Logs and alerts", [["logs", "Logs"], ["alarms", "Alarms"], ["metrics", "Metrics"]]],
  ["audit", "Audit", [["audit", "Audit log"]]],
];
const SECTIONS = TABS.flatMap(([, , items]) => items.map(([key]) => key));
const NEEDS_WINDOW = new Set(["overview", "routes", "errors", "jobs", "flows", "events", "webhooks", "mail"]);
// Links and bookmarks from before the sections were merged.
const RENAMED = { queues: "jobs", workers: "jobs", database: "overview", cache: "overview", "event-log": "events" };

const fromHash = () => {
  try {
    const key = window.location.hash.replace("#", "");
    const mapped = RENAMED[key] || key;
    return SECTIONS.includes(mapped) ? mapped : "overview";
  } catch { return "overview"; }
};
const savedMinutes = () => {
  try { const n = Number(localStorage.getItem("pawabase.observability.minutes")); return WINDOWS.some(([v]) => v === n) ? n : 60; } catch { return 60; }
};

function ago(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 5 ? "just now" : s < 90 ? `${s}s ago` : `${Math.round(s / 60)}m ago`;
}

export default function Observability({ env }) {
  const base = envPath(env);
  const [section, setSection] = useState(fromHash);
  const [minutes, setMinutes] = useState(savedMinutes);
  const [live, setLive] = useState(true);
  const [route, setRoute] = useState(null);
  const [detail, setDetail] = useState(null);
  const [emitting, setEmitting] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const system = useSystem(env, minutes, live);

  const open = (key) => {
    if (!key) return;
    if (key === "schedules") { router.visit(envHref(env, "schedules")); return; }
    setSection(key);
    try { window.history.replaceState(null, "", `#${key}`); } catch { /* the hash is a convenience */ }
  };
  useEffect(() => {
    const onHash = () => setSection(fromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    try { localStorage.setItem("pawabase.observability.minutes", String(minutes)); } catch { /* optional */ }
  }, [minutes]);
  useEffect(() => { if (system.data) setUpdatedAt(Date.now()); }, [system.data]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(timer);
  }, []);

  const openTrace = async (requestId) => {
    if (requestId) setDetail(await get(`${base}/requests/${encodeURIComponent(requestId)}`));
  };

  // Findings per section, so a dot says where to look before you click.
  const flagged = useMemo(() => {
    const out = {};
    for (const p of system.data?.problems || []) {
      const key = AREA_SECTION[p.area];
      if (key) out[key] = out[key] === "critical" || p.severity === "critical" ? "critical" : "warning";
    }
    return out;
  }, [system.data]);

  const subnav = {
    title: "Activity",
    groups: TABS.map(([key, label, tabItems]) => ({ title: key === "overview" ? undefined : label, items: tabItems.map(([k, l]) => ({ key: k, label: l, flag: flagged[k] })) })),
    active: section,
    onSelect: open,
  };
  const shared = { env, minutes, system, onOpen: open };

  return (
    <Layout title="Activity" subnav={subnav}>
      <PageHead
        title="Activity"
        description={<>What your app is doing and what went wrong, across <b>{env}</b>.</>}
        actions={<>
          <button type="button" className={`live inline-flex items-center gap-[7px] text-ui-sm text-muted bg-none border-0 font-inherit cursor-pointer p-[4px_6px] rounded-sm [&:hover]:bg-sunken [&_i]:w-2 [&_i]:h-2 [&_i]:rounded-full [&_i]:bg-ok [&_i]:shadow-none [&_i]:animate-[live-pulse_1.8s_infinite] [&.paused_i]:bg-[var(--faint)] [&.paused_i]:animate-[none] h-control-sm ${live ? "" : "paused"}`} onClick={() => setLive(!live)} title={live ? "Pause automatic refresh" : "Resume automatic refresh"}>
            <i />{live ? "Live" : "Paused"} · {ago(now - updatedAt)}
          </button>
          <button type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text sm" onClick={() => system.reload()} aria-label="Refresh now"><Icon name="play" size={14} />Refresh</button>
          {section === "events" && <Button onClick={() => setEmitting(true)}>Send test event</Button>}
          {NEEDS_WINDOW.has(section) && <Segmented options={WINDOWS} value={minutes} onChange={setMinutes} />}
        </>}
      />
      <div className="obs stack flex flex-col gap-3 [&.lg]:gap-4 lg">
        <div className="stack flex flex-col gap-3 [&.lg]:gap-4 lg" style={{ minWidth: 0 }}>
          {section === "overview" && <Overview {...shared} />}
          {section === "routes" && <Routes env={env} minutes={minutes} onRoute={setRoute} onTrace={openTrace} />}
          {section === "errors" && <Errors env={env} minutes={minutes} onRoute={setRoute} onTrace={openTrace} />}
          {section === "requests" && <Requests base={base} onTrace={openTrace} />}
          {section === "jobs" && <JobsPanel env={env} system={system} />}
          {section === "flows" && <><Flows {...shared} /><FlowRuns env={env} /></>}
          {section === "events" && <><Events {...shared} /><EventLog env={env} /></>}
          {section === "event-map" && <EventMap env={env} />}
          {section === "webhooks" && <Webhooks {...shared} />}
          {section === "mail" && <Mail {...shared} />}
          {section === "realtime" && <RealtimeHealth env={env} />}
          {section === "metrics" && <Metrics env={env} />}
          {section === "logs" && <Logs env={env} onTrace={openTrace} />}
          {section === "alarms" && <Alarms env={env} />}
          {section === "audit" && <AuditLog env={env} />}
        </div>
      </div>
      {route && <RouteDetail env={env} route={route} minutes={minutes} onClose={() => setRoute(null)} onTrace={openTrace} />}
      {emitting && <EmitEvent env={env} onClose={() => setEmitting(false)} />}
      {detail && <RequestTrace value={detail} onClose={() => setDetail(null)} />}
    </Layout>
  );
}
