import { Badge, Card, Loading, Table, when } from "../../../components/ui";
import { useApi } from "../../../lib/api";

// Live state for this environment: its busiest channels, who is connected, and what failed lately.
export default function RealtimeHealth({ env }) {
  const channels = useApi(`/${env}/channels`, { service: "realtime", interval: 5000 });
  const connections = useApi(`/${env}/connections`, { service: "realtime", interval: 5000 });
  const activity = useApi(`/${env}/activity`, { service: "realtime", interval: 5000 });
  const mine = connections.data?.data?.length;
  const errors = activity.data?.errors || [];
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
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
