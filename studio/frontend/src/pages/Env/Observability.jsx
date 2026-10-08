import { useEffect, useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Segmented } from "../../components/ui";
import { envPath, get } from "../../lib/api";
import AuditLog from "./observability/Audit";
import { CacheAndMetrics, Database } from "./observability/Data";
import Errors from "./observability/Errors";
import Flows from "./observability/Flows";
import { Events, Mail, Webhooks } from "./observability/Messaging";
import Overview from "./observability/Overview";
import Queues from "./observability/Queues";
import RealtimeHealth from "./observability/RealtimeHealth";
import Requests from "./observability/Requests";
import RequestTrace from "./observability/RequestTrace";
import RouteDetail from "./observability/RouteDetail";
import Routes from "./observability/Routes";
import Workers from "./observability/Workers";
import { AREA_SECTION, useSystem } from "./observability/system";

const WINDOWS = [[15, "15m"], [60, "1h"], [360, "6h"], [1440, "24h"], [10080, "7d"], [20160, "14d"]];

// Tabs answer "which part of the system"; pills pick the view inside it.
const TABS = [
  ["overview", "Overview", [["overview", "Overview"]]],
  ["traffic", "Traffic", [["routes", "Routes"], ["errors", "Errors"], ["requests", "Requests"]]],
  ["background", "Background", [["queues", "Queues & jobs"], ["workers", "Workers & schedules"], ["flows", "Flows"]]],
  ["messaging", "Messaging", [["events", "Events"], ["webhooks", "Webhooks"], ["mail", "Mail"], ["realtime", "Realtime"]]],
  ["data", "Data", [["database", "Database & storage"], ["cache", "Cache & metrics"]]],
  ["audit", "Audit", [["audit", "Audit log"]]],
];
const SECTIONS = TABS.flatMap(([, , items]) => items.map(([key]) => key));
const NEEDS_WINDOW = new Set(["overview", "routes", "errors", "queues", "workers", "flows", "events", "webhooks", "mail", "cache"]);

const fromHash = () => {
  try {
    const key = window.location.hash.replace("#", "");
    return SECTIONS.includes(key) ? key : "overview";
  } catch { return "overview"; }
};
const savedMinutes = () => {
  try { const n = Number(localStorage.getItem("pawabase.observability.minutes")); return WINDOWS.some(([v]) => v === n) ? n : 60; } catch { return 60; }
};
const tabOf = (section) => TABS.find(([, , items]) => items.some(([key]) => key === section));

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
  const [updatedAt, setUpdatedAt] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const system = useSystem(env, minutes, live);

  const open = (key) => {
    if (!key) return;
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
  const tabFlag = (items) => items.reduce((level, [key]) => (flagged[key] === "critical" ? "critical" : flagged[key] && level !== "critical" ? "warning" : level), null);

  const [tabKey, , items] = tabOf(section);
  const shared = { env, minutes, system, onOpen: open };

  return (
    <Layout title="Observability">
      <div className="stack lg">
        <div className="obs-bar">
          <div>
            <h1 style={{ margin: 0, fontSize: 28 }}>Observability</h1>
            <p className="muted" style={{ margin: "4px 0 0" }}>Traffic, background work, messaging and data across <b>{env}</b>.</p>
          </div>
          <div className="row" style={{ gap: 10 }}>
            <button type="button" className={`live ${live ? "" : "paused"}`} onClick={() => setLive(!live)} title={live ? "Pause automatic refresh" : "Resume automatic refresh"}>
              <i />{live ? "Live" : "Paused"} · {ago(now - updatedAt)}
            </button>
            <button type="button" className="btn sm" onClick={() => system.reload()} aria-label="Refresh now"><Icon name="play" size={14} />Refresh</button>
            {NEEDS_WINDOW.has(section) && <Segmented options={WINDOWS} value={minutes} onChange={setMinutes} />}
          </div>
        </div>

        <div>
          <div className="obs-tabs" role="tablist">
            {TABS.map(([key, label, tabItems]) => {
              const flag = tabFlag(tabItems);
              return (
                <button key={key} type="button" role="tab" aria-selected={key === tabKey} className={`obs-tab ${key === tabKey ? "active" : ""}`}
                  onClick={() => open(key === tabKey ? section : tabItems[0][0])}>
                  {label}{flag && <i className={`obs-dot ${flag}`} />}
                </button>
              );
            })}
          </div>
          {items.length > 1 && (
            <div className="obs-pills" style={{ marginTop: 14 }}>
              {items.map(([key, label]) => (
                <button key={key} type="button" className={`obs-pill ${key === section ? "active" : ""}`} onClick={() => open(key)}>
                  {label}{flagged[key] && <i className={`obs-dot ${flagged[key]}`} />}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="stack lg" style={{ minWidth: 0 }}>
          {section === "overview" && <Overview {...shared} />}
          {section === "routes" && <Routes env={env} minutes={minutes} onRoute={setRoute} onTrace={openTrace} />}
          {section === "errors" && <Errors env={env} minutes={minutes} onRoute={setRoute} onTrace={openTrace} />}
          {section === "requests" && <Requests base={base} onTrace={openTrace} />}
          {section === "queues" && <Queues {...shared} />}
          {section === "workers" && <Workers {...shared} />}
          {section === "flows" && <Flows {...shared} />}
          {section === "events" && <Events {...shared} />}
          {section === "webhooks" && <Webhooks {...shared} />}
          {section === "mail" && <Mail {...shared} />}
          {section === "realtime" && <RealtimeHealth env={env} />}
          {section === "database" && <Database env={env} />}
          {section === "cache" && <CacheAndMetrics env={env} minutes={minutes} />}
          {section === "audit" && <AuditLog env={env} />}
        </div>
      </div>
      {route && <RouteDetail env={env} route={route} minutes={minutes} onClose={() => setRoute(null)} onTrace={openTrace} />}
      {detail && <RequestTrace value={detail} onClose={() => setDetail(null)} />}
    </Layout>
  );
}
