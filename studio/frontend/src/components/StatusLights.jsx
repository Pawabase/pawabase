import { useEffect, useState } from "react";

// One compact platform light, checked every 30 seconds. Hover or focus reveals
// the individual services and their response times.

const LABEL = { up: "Operational", down: "Unreachable", unknown: "Not reporting" };

export default function StatusLights() {
  const [services, setServices] = useState(null);
  const [checkedAt, setCheckedAt] = useState(null);

  useEffect(() => {
    let alive = true;
    async function check() {
      try {
        const response = await fetch("/studio/status", { credentials: "same-origin", headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error(String(response.status));
        const data = await response.json();
        if (alive) {
          setServices(data.services);
          setCheckedAt(new Date());
        }
      } catch {
        if (alive) setServices((s) => (s || []).map((x) => ({ ...x, status: "unknown" })));
      }
    }
    check();
    const timer = setInterval(check, 30000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (!services) return <div className="lights" aria-label="Checking system status"><span className="light pending" /></div>;
  const down = services.filter((s) => s.status === "down").length;
  const summary = down ? `${down} service${down > 1 ? "s" : ""} down` : "All systems operational";
  const overall = down ? "down" : services.some((s) => s.status !== "up") ? "unknown" : "up";
  return (
    <div className="lights" tabIndex={0} aria-label={summary}>
      <span className={`light ${overall}`} />
      <div className="lights-pop" role="tooltip">
        <div className="lights-head">
          <b>{summary}</b>
          {checkedAt && <span className="faint">checked {checkedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>}
        </div>
        {services.map((s) => (
          <div key={s.name} className="lights-row" title={s.detail || ""}>
            <span className={`light ${s.status}`} />
            <span className="grow">{s.name}</span>
            <span className="muted">{s.latency_ms != null && s.status === "up" ? `${Math.round(s.latency_ms)} ms` : s.detail && s.status !== "down" ? s.detail : LABEL[s.status]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
