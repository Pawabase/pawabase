import { Badge } from "../../../components/ui";

export const statusTone = (status) => (status >= 500 ? "red" : status >= 400 ? "yellow" : "green");

export const ms = (value) => {
  const n = Number(value || 0);
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)} s`;
  if (n >= 100) return `${Math.round(n)} ms`;
  return `${n.toFixed(n >= 10 ? 0 : 1)} ms`;
};

export const percent = (value) => `${Number(value || 0).toFixed(Number(value) % 1 ? 1 : 0)}%`;

// One colour per step kind, so a waterfall and a "time by kind" bar read the same.
export const KIND_COLOR = {
  db: "var(--brand)",
  cache: "var(--ok)",
  http: "var(--warn)",
  storage: "var(--info)",
  event: "#c0709a",
  job: "#7a8c3c",
  flow: "#3c8c8c",
  function: "#8c6a3c",
  policy: "var(--faint)",
  mail: "#b0603c",
  realtime: "#5a7ad0",
};
export const kindColor = (kind) => KIND_COLOR[kind] || "var(--muted)";

export function MethodBadge({ method }) {
  return <Badge tone="blue">{method}</Badge>;
}

export function RateBar({ value, tone }) {
  const width = Math.min(100, Math.max(value > 0 ? 3 : 0, value));
  return (
    <div className="rate-bar" title={percent(value)}>
      <div style={{ width: `${width}%`, background: tone }} />
    </div>
  );
}

/** Per-bucket requests with the failing part stacked on top. */
export function Timeline({ points, height = 90 }) {
  const max = Math.max(...points.map((p) => p.requests), 1);
  return (
    <div className="timeline" style={{ height }}>
      {points.map((p) => {
        const ok = p.requests - p.server_errors - p.client_errors;
        const title = `${new Date(p.at).toLocaleString()}\n${p.requests} requests · ${p.server_errors} server errors · ${p.client_errors} client errors · p95 ${ms(p.p95_ms)}`;
        return (
          <div key={p.at} className="timeline-col" title={title}>
            <div style={{ height: `${(p.server_errors / max) * 100}%`, background: "var(--danger)" }} />
            <div style={{ height: `${(p.client_errors / max) * 100}%`, background: "var(--warn)" }} />
            <div style={{ height: `${(Math.max(ok, 0) / max) * 100}%`, background: "var(--lavender)" }} />
          </div>
        );
      })}
    </div>
  );
}

export function KindBars({ items }) {
  if (!items?.length) return <div className="faint">No steps were recorded for these requests.</div>;
  return (
    <div className="stack">
      <div className="kind-strip">
        {items.map((k) => <div key={k.kind} title={`${k.kind} ${ms(k.total_ms)}`} style={{ flex: Math.max(k.share, 0.01), background: kindColor(k.kind) }} />)}
      </div>
      <div className="kind-legend">
        {items.map((k) => (
          <div key={k.kind} className="row">
            <span className="swatch" style={{ background: kindColor(k.kind) }} />
            <b>{k.kind}</b>
            <span className="faint">{k.calls} calls · avg {ms(k.avg_ms)} · {Math.round(k.share * 100)}% of time</span>
            {k.errors > 0 && <Badge tone="red">{k.errors} failed</Badge>}
          </div>
        ))}
      </div>
    </div>
  );
}
