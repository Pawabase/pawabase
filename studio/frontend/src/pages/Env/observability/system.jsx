import { Badge, Card, Empty } from "../../../components/ui";
import Chart from "../../../components/Chart";
import { envPath, useApi } from "../../../lib/api";

// One report feeds every non-HTTP section, so the sidebar badges, the overview
// and the section pages always agree with each other.
export function useSystem(env, minutes, live = true) {
  return useApi(`${envPath(env)}/observability/system`, { params: { minutes }, interval: live ? 15000 : undefined });
}

export const COLOR = {
  ok: "var(--ok)", bad: "var(--danger)", warn: "var(--warn)", info: "var(--info)", brand: "var(--brand)", mint: "var(--chart-mint)", faint: "var(--faint)",
};

/** Which sidebar section explains each kind of finding. */
export const AREA_SECTION = { queues: "queues", workers: "workers", schedules: "workers", flows: "flows", webhooks: "webhooks", mail: "mail", realtime: "realtime" };

export const pct = (v) => (v == null ? "—" : `${v}%`);
export const num = (v) => Number(v || 0).toLocaleString();

export function span(seconds) {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export function Legend({ items }) {
  return <div className="legend">{items.map(([label, color]) => <span key={label}><i className="swatch" style={{ background: color }} />{label}</span>)}</div>;
}

/** A titled time-series card. `lines` are [key, label, color, type] tuples. */
export function SeriesCard({ title, window, data, lines, height = 200, empty }) {
  return (
    <Card title={title} className="chart-card" actions={<Legend items={lines.map(([, label, color]) => [label, color])} />}>
      <Chart
        height={height}
        stepSeconds={window?.step_seconds}
        data={data || []}
        series={lines.map(([key, label, color, type]) => ({ key, label, color, type: type || "area" }))}
        empty={empty || "Nothing recorded in this window."}
      />
    </Card>
  );
}

/** A rate shown as a coloured badge: green when high, red when low. */
export function RateBadge({ value, good = 98, warn = 90 }) {
  if (value == null) return <span className="faint">—</span>;
  return <Badge tone={value >= good ? "green" : value >= warn ? "yellow" : "red"}>{value}%</Badge>;
}

export function Problems({ problems, onOpen }) {
  if (!problems?.length) return <Empty>Nothing needs attention in this window.</Empty>;
  return (
    <div className="stack" style={{ gap: 8, padding: 4 }}>
      {problems.map((p, i) => (
        <button key={i} type="button" onClick={() => onOpen?.(AREA_SECTION[p.area])} className={`alert ${p.severity === "critical" ? "error" : "warn"}`} style={{ textAlign: "left", border: 0, cursor: "pointer", font: "inherit" }}>
          <b style={{ textTransform: "capitalize" }}>{p.area}</b> · {p.message}
        </button>
      ))}
    </div>
  );
}
