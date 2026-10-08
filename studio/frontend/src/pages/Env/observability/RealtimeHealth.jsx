import { Link } from "@inertiajs/react";
import { Badge, Card, Loading, Table, when } from "../../../components/ui";
import { Stat, StatStrip } from "./kit";
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
    <div className="stack lg">
      <StatStrip>
        <Stat tone="sky" icon="users" value={mine ?? "–"} label="connections (this environment)" />
        <Stat tone="lavender" icon="realtime" value={channels.data?.data?.length ?? "–"} label="active channels" />
        <Stat tone={errors.length ? "rose" : "mint"} icon="pulse" value={errors.length} label="recent policy errors" />
        <Stat tone={stats.dropped || stats.failed ? "butter" : "mint"} icon="clock" value={`${num(stats.dropped)} / ${num(stats.failed)}`} label={`dropped / failed sends · up ${stats.uptime_seconds != null ? span(stats.uptime_seconds) : "–"}`} />
      </StatStrip>
      <Card title="Realtime service" actions={<Link className="link-more" href={envHref(env, "realtime")}>Open console</Link>}>
        <Loading state={summary}>
          {() => <div className="row wrap" style={{ gap: 24 }}>
            <span><b>{num(stats.connections)}</b> <span className="muted">connections, all environments</span></span>
            <span><b>{num(stats.channels)}</b> <span className="muted">channels</span></span>
            <span><b>{num(stats.subscriptions)}</b> <span className="muted">subscriptions</span></span>
            <span className="muted">fan-out <Badge>{stats.fanout || "memory"}</Badge></span>
            {counters.map(([k, v]) => <span key={k}><b>{num(v)}</b> <span className="muted">{k.replace(/_/g, " ")}</span></span>)}
          </div>}
        </Loading>
      </Card>
      <div className="grid two">
        <Card flush title="Busiest channels">
          <Loading state={channels} empty="No active channels.">
            {(d) => <Table rows={[...(d.data || [])].sort((a, b) => (b.subscribers || 0) - (a.subscribers || 0)).slice(0, 10).map((c) => ({ ...c, id: c.name }))} columns={[
              { label: "Channel", render: (c) => <code>{c.name}</code> }, { label: "Subscribers", key: "subscribers" }, { label: "Present", key: "presence" },
            ]} />}
          </Loading>
        </Card>
        <Card flush title="Policy errors">
          <Table rows={errors.slice(0, 10)} empty="No rejected publishes or subscriptions." columns={[
            { label: "Channel", render: (e) => <code>{e.channel}</code> }, { label: "Action", render: (e) => <Badge>{e.type}</Badge> }, { label: "Reason", render: (e) => <span className="error-text">{e.error}</span> },
          ]} />
        </Card>
      </div>
      <Card flush title="Latest deliveries">
        <Table rows={(activity.data?.deliveries || []).slice(0, 15)} empty="Nothing has been delivered recently." columns={[
          { label: "When", render: (d) => d.at ? when(d.at) : "—" }, { label: "Channel", render: (d) => <code>{d.channel}</code> }, { label: "Event", key: "event" }, { label: "Delivered", key: "delivered" },
          { label: "Dropped", render: (d) => d.dropped ? <Badge tone="yellow">{d.dropped}</Badge> : 0 }, { label: "Failed", render: (d) => d.failed ? <Badge tone="red">{d.failed}</Badge> : 0 },
        ]} />
      </Card>
    </div>
  );
}
