import { Link } from "@inertiajs/react";
import { Badge, Card, Loading, Table, when } from "../../../components/ui";
import { envHref } from "../../../components/Layout";
import { useApi } from "../../../lib/api";
import { num, span } from "./system";

// Live state, not history: Angula keeps connections and recent deliveries in memory.
export default function RealtimeHealth({ env }) {
  const summary = useApi("/summary", { service: "realtime", interval: 5000 });
  const channels = useApi(`/${env}/channels`, { service: "realtime", interval: 5000 });
  const connections = useApi(`/${env}/connections`, { service: "realtime", interval: 5000 });
  const activity = useApi(`/${env}/activity`, { service: "realtime", interval: 5000 });
  const mine = connections.data?.data?.length;
  const errors = activity.data?.errors || [];
  const stats = summary.data || {};
  const counters = Object.entries(stats).filter(([k, v]) => typeof v === "number" && !["connections", "channels", "subscriptions", "uptime_seconds"].includes(k));
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <Card icon="realtime" title="Realtime service" actions={<Link className="link-more inline-flex items-center gap-0.5 text-ui-sm font-semibold text-muted [&:hover]:text-text" href={envHref(env, "realtime")}>Open console</Link>}>
        <Loading state={summary}>
          {() => <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 24 }}>
            <span><b>{num(stats.connections)}</b> <span className="muted text-muted">connections, all environments</span></span>
            <span><b>{num(stats.channels)}</b> <span className="muted text-muted">channels</span></span>
            <span><b>{num(stats.subscriptions)}</b> <span className="muted text-muted">subscriptions</span></span>
            <span className="muted text-muted">fan-out <Badge>{stats.fanout || "memory"}</Badge></span>
            {counters.map(([k, v]) => <span key={k}><b>{num(v)}</b> <span className="muted text-muted">{k.replace(/_/g, " ")}</span></span>)}
          </div>}
        </Loading>
      </Card>
      <div className="grid grid gap-3.5 grid-cols-[repeat(auto-fill,_minmax(220px,_1fr))] [&.wide]:grid-cols-[repeat(auto-fill,_minmax(320px,_1fr))] [&.two]:grid-cols-2 [&.three]:grid-cols-3 max-wide:[&.three.insights]:grid-cols-2 max-navigation:[&.two]:grid-cols-1 max-navigation:[&.three]:grid-cols-1 max-navigation:[&.three.insights]:grid-cols-1 [&:has(>_.tile)]:grid [&:has(>_.tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.tile)]:gap-0 [&:has(>_.tile)]:[border:1px_solid_var(--line)] [&:has(>_.tile)]:rounded-sm [&:has(>_.tile)]:overflow-hidden [&:has(>_.tile)]:mb-4 [&:has(>_.stat-tile)]:grid [&:has(>_.stat-tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.stat-tile)]:gap-0 [&:has(>_.stat-tile)]:[border:1px_solid_var(--line)] [&:has(>_.stat-tile)]:rounded-sm [&:has(>_.stat-tile)]:overflow-hidden [&:has(>_.stat-tile)]:mb-4 two">
        <Card icon="realtime" flush title="Busiest channels">
          <Loading state={channels} empty="No active channels.">
            {(d) => <Table rows={[...(d.data || [])].sort((a, b) => (b.subscribers || 0) - (a.subscribers || 0)).slice(0, 10).map((c) => ({ ...c, id: c.name }))} columns={[
              { label: "Channel", render: (c) => <code>{c.name}</code> }, { label: "Subscribers", key: "subscribers" }, { label: "Present", key: "presence" },
            ]} />}
          </Loading>
        </Card>
        <Card icon="policies" flush title="Policy errors">
          <Table rows={errors.slice(0, 10)} empty="No rejected publishes or subscriptions." columns={[
            { label: "Channel", render: (e) => <code>{e.channel}</code> }, { label: "Action", render: (e) => <Badge>{e.type}</Badge> }, { label: "Reason", render: (e) => <span className="error-text text-danger text-ui-sm font-medium">{e.error}</span> },
          ]} />
        </Card>
      </div>
      <Card icon="send" flush title="Latest deliveries">
        <Table rows={(activity.data?.deliveries || []).slice(0, 15)} empty="Nothing has been delivered recently." columns={[
          { label: "When", render: (d) => d.at ? when(d.at) : "—" }, { label: "Channel", render: (d) => <code>{d.channel}</code> }, { label: "Event", key: "event" }, { label: "Delivered", key: "delivered" },
          { label: "Dropped", render: (d) => d.dropped ? <Badge tone="yellow">{d.dropped}</Badge> : 0 }, { label: "Failed", render: (d) => d.failed ? <Badge tone="red">{d.failed}</Badge> : 0 },
        ]} />
      </Card>
    </div>
  );
}
