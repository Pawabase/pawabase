import { useState } from "react";
import { Badge } from "../../../components/ui";
import { kindColor, ms } from "./shared";

/** Every timed step of a request on one time axis, nested under the step that started it. */
export default function Waterfall({ spans, total }) {
  const [open, setOpen] = useState(null);
  if (!spans?.length) return <div className="faint text-faint">No steps were timed for this request (it predates step tracing, or it did no work).</div>;
  const end = Math.max(total || 0, ...spans.map((s) => s.start_ms + s.duration_ms), 1);
  const depth = {};
  const byId = Object.fromEntries(spans.map((s) => [s.id, s]));
  const levelOf = (s) => (s.parent && byId[s.parent] ? (depth[s.id] ??= levelOf(byId[s.parent]) + 1) : 0);
  const rows = [...spans].sort((a, b) => a.start_ms - b.start_ms || a.id - b.id);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div className="waterfall relative text-ui-sm">
      <div className="waterfall-axis relative h-[18px] ml-[38%] text-faint text-2xs [&_span]:absolute [&_span]:transform-[translateX(-50%)] [&_span:first-child]:transform-[none] [&_span:last-child]:transform-[translateX(-100%)]">{ticks.map((t) => <span key={t} style={{ left: `${t * 100}%` }}>{ms(end * t)}</span>)}</div>
      {rows.map((s) => {
        const left = (s.start_ms / end) * 100;
        const width = Math.max((s.duration_ms / end) * 100, 0.4);
        const selected = open === s.id;
        return (
          <div key={`${s.service}-${s.id}`}>
            <div className={`waterfall-row flex items-center min-h-[26px] cursor-pointer rounded-sm [&:hover]:bg-sunken [&.selected]:bg-sunken ${selected ? "selected" : ""}`} onClick={() => setOpen(selected ? null : s.id)}>
              <div className="waterfall-label w-[38%] flex-none flex items-center gap-1.5 overflow-hidden pr-2 [&_code]:overflow-hidden [&_code]:text-ellipsis [&_code]:whitespace-nowrap" style={{ paddingLeft: levelOf(s) * 14 }}>
                <span className="swatch w-2.5 h-2.5 rounded-xs inline-block flex-none" style={{ background: kindColor(s.kind) }} />
                <span className="waterfall-kind font-semibold text-muted">{s.kind}</span>
                <code title={s.name}>{s.name}</code>
              </div>
              <div className="waterfall-track relative flex-1 h-4 bg-[linear-gradient(to_right,_transparent_calc(25%_-_1px),_var(--sunken)_calc(25%_-_1px),_var(--sunken)_25%,_transparent_25%,_transparent_calc(50%_-_1px),_var(--sunken)_calc(50%_-_1px),_var(--sunken)_50%,_transparent_50%,_transparent_calc(75%_-_1px),_var(--sunken)_calc(75%_-_1px),_var(--sunken)_75%,_transparent_75%)]">
                <div className={`waterfall-bar absolute top-[3px] h-2.5 rounded-xs min-w-0.5 ${s.status === "error" ? "failed" : ""}`} style={{ left: `${left}%`, width: `${width}%`, background: s.status === "error" ? "var(--danger)" : kindColor(s.kind) }} />
                <span className="waterfall-ms absolute top-0 text-2xs text-muted pl-1.5 whitespace-nowrap" style={{ left: `${Math.min(left + width, 88)}%` }}>{ms(s.duration_ms)}</span>
              </div>
            </div>
            {selected && (
              <div className="waterfall-detail m-[4px_0_10px_12px] grid gap-2">
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
                  {s.status === "error" && <Badge tone="red">failed</Badge>}
                  <span>starts at {ms(s.start_ms)}</span>
                  <span>takes {ms(s.duration_ms)}</span>
                  {s.service && <span className="faint text-faint">{s.service}</span>}
                </div>
                <pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg">{s.name}</pre>
                {Object.keys(s.attrs || {}).length > 0 && <pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg">{JSON.stringify(s.attrs, null, 2)}</pre>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
