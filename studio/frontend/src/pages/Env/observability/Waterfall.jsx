import { useState } from "react";
import { Badge } from "../../../components/ui";
import { kindColor, ms } from "./shared";

/** Every timed step of a request on one time axis, nested under the step that started it. */
export default function Waterfall({ spans, total }) {
  const [open, setOpen] = useState(null);
  if (!spans?.length) return <div className="faint">No steps were timed for this request (it predates step tracing, or it did no work).</div>;
  const end = Math.max(total || 0, ...spans.map((s) => s.start_ms + s.duration_ms), 1);
  const depth = {};
  const byId = Object.fromEntries(spans.map((s) => [s.id, s]));
  const levelOf = (s) => (s.parent && byId[s.parent] ? (depth[s.id] ??= levelOf(byId[s.parent]) + 1) : 0);
  const rows = [...spans].sort((a, b) => a.start_ms - b.start_ms || a.id - b.id);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div className="waterfall">
      <div className="waterfall-axis">{ticks.map((t) => <span key={t} style={{ left: `${t * 100}%` }}>{ms(end * t)}</span>)}</div>
      {rows.map((s) => {
        const left = (s.start_ms / end) * 100;
        const width = Math.max((s.duration_ms / end) * 100, 0.4);
        const selected = open === s.id;
        return (
          <div key={`${s.service}-${s.id}`}>
            <div className={`waterfall-row ${selected ? "selected" : ""}`} onClick={() => setOpen(selected ? null : s.id)}>
              <div className="waterfall-label" style={{ paddingLeft: levelOf(s) * 14 }}>
                <span className="swatch" style={{ background: kindColor(s.kind) }} />
                <span className="waterfall-kind">{s.kind}</span>
                <code title={s.name}>{s.name}</code>
              </div>
              <div className="waterfall-track">
                <div className={`waterfall-bar ${s.status === "error" ? "failed" : ""}`} style={{ left: `${left}%`, width: `${width}%`, background: s.status === "error" ? "var(--danger)" : kindColor(s.kind) }} />
                <span className="waterfall-ms" style={{ left: `${Math.min(left + width, 88)}%` }}>{ms(s.duration_ms)}</span>
              </div>
            </div>
            {selected && (
              <div className="waterfall-detail">
                <div className="row wrap">
                  {s.status === "error" && <Badge tone="red">failed</Badge>}
                  <span>starts at {ms(s.start_ms)}</span>
                  <span>takes {ms(s.duration_ms)}</span>
                  {s.service && <span className="faint">{s.service}</span>}
                </div>
                <pre className="code-block">{s.name}</pre>
                {Object.keys(s.attrs || {}).length > 0 && <pre className="code-block">{JSON.stringify(s.attrs, null, 2)}</pre>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
