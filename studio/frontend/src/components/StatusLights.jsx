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

  if (!services) return <div className="lights relative flex items-center gap-[5px] p-[8px_10px] rounded-[99px] cursor-default outline-none [&:hover]:bg-surface [&:focus-visible]:bg-surface [&_.light]:w-2 [&_.light]:h-2 [&_.light]:rounded-full [&_.light]:bg-[var(--faint)] [&_.light]:flex-none [&_.light.up]:bg-ok [&_.light.up]:shadow-none [&_.light.down]:bg-danger [&_.light.down]:shadow-none [&_.light.down]:animate-[blink_1.4s_ease-in-out_infinite] [&_.light.unknown]:bg-[var(--line-2)] [&_.light.pending]:bg-[var(--line-2)] [&_.light.pending]:animate-[blink_1.4s_ease-in-out_infinite] [&:hover_.lights-pop]:opacity-100 [&:hover_.lights-pop]:transform-[none] [&:hover_.lights-pop]:pointer-events-auto [&:focus-within_.lights-pop]:opacity-100 [&:focus-within_.lights-pop]:transform-[none] [&:focus-within_.lights-pop]:pointer-events-auto max-navigation:p-[8px_6px] max-navigation:gap-1 max-navigation:[&_.light]:w-[7px] max-navigation:[&_.light]:h-[7px]" aria-label="Checking system status"><span className="light pending" /></div>;
  const down = services.filter((s) => s.status === "down").length;
  const summary = down ? `${down} service${down > 1 ? "s" : ""} down` : "All systems operational";
  const overall = down ? "down" : services.some((s) => s.status !== "up") ? "unknown" : "up";
  return (
    <div className="lights relative flex items-center gap-[5px] p-[8px_10px] rounded-[99px] cursor-default outline-none [&:hover]:bg-surface [&:focus-visible]:bg-surface [&_.light]:w-2 [&_.light]:h-2 [&_.light]:rounded-full [&_.light]:bg-[var(--faint)] [&_.light]:flex-none [&_.light.up]:bg-ok [&_.light.up]:shadow-none [&_.light.down]:bg-danger [&_.light.down]:shadow-none [&_.light.down]:animate-[blink_1.4s_ease-in-out_infinite] [&_.light.unknown]:bg-[var(--line-2)] [&_.light.pending]:bg-[var(--line-2)] [&_.light.pending]:animate-[blink_1.4s_ease-in-out_infinite] [&:hover_.lights-pop]:opacity-100 [&:hover_.lights-pop]:transform-[none] [&:hover_.lights-pop]:pointer-events-auto [&:focus-within_.lights-pop]:opacity-100 [&:focus-within_.lights-pop]:transform-[none] [&:focus-within_.lights-pop]:pointer-events-auto max-navigation:p-[8px_6px] max-navigation:gap-1 max-navigation:[&_.light]:w-[7px] max-navigation:[&_.light]:h-[7px]" tabIndex={0} aria-label={summary}>
      <span className={`light ${overall}`} />
      <div className="lights-pop absolute right-0 top-[calc(100%_+_8px)] w-[270px] bg-surface rounded-sm shadow-none p-1.5 opacity-0 transform-[translateY(-4px)] pointer-events-none transition-[opacity_0.15s,_transform_0.15s] z-[30] [border:1px_solid_var(--line-2)]" role="tooltip">
        <div className="lights-head flex flex-col gap-px p-[4px_6px_10px] [border-bottom:1px_solid_var(--line)] mb-1.5 text-ui [&_span]:text-caption">
          <b>{summary}</b>
          {checkedAt && <span className="faint text-faint">checked {checkedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>}
        </div>
        {services.map((s) => (
          <div key={s.name} className="lights-row flex items-center gap-2.5 p-1.5 text-ui rounded-sm [&_.muted]:text-xs" title={s.detail || ""}>
            <span className={`light ${s.status}`} />
            <span className="grow flex-1 min-w-0">{s.name}</span>
            <span className="muted text-muted">{s.latency_ms != null && s.status === "up" ? `${Math.round(s.latency_ms)} ms` : s.detail && s.status !== "down" ? s.detail : LABEL[s.status]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
