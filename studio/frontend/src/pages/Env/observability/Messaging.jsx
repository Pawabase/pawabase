import { Link } from "@inertiajs/react";
import { Badge, Card, Loading, Table, when } from "../../../components/ui";
import { envHref } from "../../../components/Layout";
import { COLOR, RateBadge, SeriesCard, num } from "./system";
import { ms } from "./shared";

export function Events({ env, system }) {
  return (
    <Loading state={system}>
      {(s) => {
        const e = s.events;
        return (
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
            <SeriesCard title="Event volume" window={s.window} data={e.series} lines={[["events", "Events", COLOR.mint]]} empty="No events in this window." />
            <div className="grid grid gap-3.5 grid-cols-[repeat(auto-fill,_minmax(220px,_1fr))] [&.wide]:grid-cols-[repeat(auto-fill,_minmax(320px,_1fr))] [&.two]:grid-cols-2 [&.three]:grid-cols-3 max-wide:[&.three.insights]:grid-cols-2 max-navigation:[&.two]:grid-cols-1 max-navigation:[&.three]:grid-cols-1 max-navigation:[&.three.insights]:grid-cols-1 [&:has(>_.tile)]:grid [&:has(>_.tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.tile)]:gap-0 [&:has(>_.tile)]:[border:1px_solid_var(--line)] [&:has(>_.tile)]:rounded-sm [&:has(>_.tile)]:overflow-hidden [&:has(>_.tile)]:mb-4 [&:has(>_.stat-tile)]:grid [&:has(>_.stat-tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.stat-tile)]:gap-0 [&:has(>_.stat-tile)]:[border:1px_solid_var(--line)] [&:has(>_.stat-tile)]:rounded-sm [&:has(>_.stat-tile)]:overflow-hidden [&:has(>_.stat-tile)]:mb-4 two">
              <Card icon="events" flush title="Busiest events" actions={<Link className="link-more inline-flex items-center gap-0.5 text-ui-sm font-semibold text-muted [&:hover]:text-text" href={envHref(env, "events")}>Event log</Link>}>
                <Table rows={e.top} empty="No events in this window." columns={[{ label: "Event", render: (x) => <code>{x.name}</code> }, { label: "Count", render: (x) => num(x.count) }]} />
              </Card>
              <Card icon="networking" flush title="Where they come from">
                <Table rows={e.sources} empty="No events in this window." columns={[{ label: "Source", render: (x) => <Badge>{x.name}</Badge> }, { label: "Count", render: (x) => num(x.count) }]} />
              </Card>
            </div>
          </div>
        );
      }}
    </Loading>
  );
}

export function Webhooks({ env, system }) {
  return (
    <Loading state={system}>
      {(s) => {
        const w = s.webhooks;
        const rate = w.totals.total ? Math.round(((w.totals.total - w.totals.failed) / w.totals.total) * 1000) / 10 : null;
        return (
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
            <SeriesCard title="Deliveries" window={s.window} data={w.series} lines={[["delivered", "Delivered", COLOR.ok], ["failed", "Failed", COLOR.bad, "line"], ["pending", "Pending", COLOR.warn, "line"]]} empty="No outbound deliveries in this window." />
            <Card icon="routes" flush title="By endpoint" actions={<Link className="link-more inline-flex items-center gap-0.5 text-ui-sm font-semibold text-muted [&:hover]:text-text" href={envHref(env, "webhooks")}>Manage webhooks</Link>}>
              <Table rows={w.by_endpoint} empty="No deliveries in this window." columns={[
                { label: "Endpoint", render: (x) => <b>{x.endpoint}</b> }, { label: "Deliveries", key: "total" },
                { label: "Failed", render: (x) => x.failed ? <span className="error-text text-danger text-ui-sm font-medium">{x.failed}</span> : 0 },
                { label: "Delivered", render: (x) => <RateBadge value={x.success_rate} good={99} warn={90} /> },
                { label: "Avg", render: (x) => x.avg_ms != null ? ms(x.avg_ms) : "—" }, { label: "p95", render: (x) => x.p95_ms != null ? ms(x.p95_ms) : "—" },
              ]} />
            </Card>
            <Card icon="pulse" flush title="Recent failures">
              <Table rows={w.recent_failures} empty="No delivery failed in this window." columns={[
                { label: "When", render: (x) => when(x.at) }, { label: "Endpoint", render: (x) => <b>{x.endpoint}</b> }, { label: "Event", render: (x) => <code>{x.event}</code> },
                { label: "HTTP", render: (x) => x.status_code ? <Badge tone="red">{x.status_code}</Badge> : <span className="faint text-faint">—</span> }, { label: "Attempts", key: "attempts" },
                { label: "Error", render: (x) => <span className="error-text text-danger text-ui-sm font-medium">{x.error.slice(0, 120)}</span> },
              ]} />
            </Card>
          </div>
        );
      }}
    </Loading>
  );
}

export function Mail({ env, system }) {
  return (
    <Loading state={system}>
      {(s) => {
        const m = s.mail;
        return (
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
            <SeriesCard title="Messages" window={s.window} data={m.series} lines={[["sent", "Sent", COLOR.ok], ["suppressed", "Suppressed", COLOR.warn, "line"], ["failed", "Failed", COLOR.bad, "line"]]} empty="No mail in this window." />
            <Card icon="mail-templates" flush title="By template" actions={<Link className="link-more inline-flex items-center gap-0.5 text-ui-sm font-semibold text-muted [&:hover]:text-text" href={envHref(env, "mail")}>Mail setup and log</Link>}>
              <Table rows={m.by_template} empty="No mail in this window." columns={[
                { label: "Template or subject", render: (x) => <b>{x.template}</b> }, { label: "Messages", key: "total" },
                { label: "Failed", render: (x) => x.failed ? <span className="error-text text-danger text-ui-sm font-medium">{x.failed}</span> : 0 },
              ]} />
            </Card>
            <Card icon="pulse" flush title="Recent failures">
              <Table rows={m.recent_failures} empty="No email failed in this window." columns={[
                { label: "When", render: (x) => when(x.at) }, { label: "Subject", key: "subject" }, { label: "Error", render: (x) => <span className="error-text text-danger text-ui-sm font-medium">{x.error.slice(0, 140)}</span> },
              ]} />
            </Card>
          </div>
        );
      }}
    </Loading>
  );
}
