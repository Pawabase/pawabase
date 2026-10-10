import { Link } from "@inertiajs/react";
import { useState } from "react";
import { Badge, Card, Loading, Table, when } from "../../../components/ui";
import { envHref } from "../../../components/Layout";
import { num } from "./system";

export default function Workers({ env, system }) {
  const [showStale, setShowStale] = useState(false);
  return (
    <Loading state={system}>
      {(s) => {
        const w = s.workers;
        const old = (x) => !x.alive && (!x.last_seen || Date.now() - new Date(x.last_seen).getTime() > 3600000);
        const stale = w.items.filter(old);
        const live = w.items.filter((x) => !old(x));
        const overdue = s.schedules.filter((x) => x.overdue).length;
        return (
          <div className="stack lg">
            <Card flush title="Workers and schedulers" actions={<span className="muted small">a process is alive when it reported in within 45 seconds</span>}>
              {stale.length > 0 && <div className="row" style={{ padding: "12px 16px", justifyContent: "space-between" }}>
                <span className="muted small">{stale.length} process{stale.length > 1 ? "es" : ""} have not reported in for over an hour — usually old runs that were stopped without a clean shutdown.</span>
                <button type="button" className="chip" onClick={() => setShowStale(!showStale)}>{showStale ? "Hide" : "Show"} stale</button>
              </div>}
              <Table rows={showStale ? w.items : live} empty="No worker has reported in. Start one with `python -m app.worker`." columns={[
                { label: "Name", key: "name" }, { label: "Kind", key: "kind" },
                { label: "State", render: (x) => <Badge tone={x.alive ? "green" : "red"}>{x.alive ? "alive" : x.status === "running" ? "stale" : x.status}</Badge> },
                { label: "Concurrency", key: "concurrency" }, { label: "Processed", render: (x) => num(x.processed) },
                { label: "Last seen", render: (x) => x.last_seen ? when(x.last_seen) : <span className="faint">—</span> },
              ]} />
            </Card>
            <Card flush title="Schedules" actions={<Link className="link-more" href={envHref(env, "schedules")}>Manage schedules</Link>}>
              <Table rows={s.schedules} empty="No schedules defined." columns={[
                { label: "Name", render: (x) => <b>{x.name}</b> },
                { label: "When", render: (x) => <code>{x.cron || (x.interval_seconds ? `every ${x.interval_seconds}s` : "—")}</code> },
                { label: "Runs", render: (x) => `${x.target_type}:${x.target}` },
                { label: "State", render: (x) => !x.enabled ? <Badge>paused</Badge> : x.overdue ? <Badge tone="red">overdue</Badge> : <Badge tone="green">on time</Badge> },
                { label: "Last status", render: (x) => x.last_status ? <Badge tone={x.last_status === "failed" ? "red" : "green"}>{x.last_status}</Badge> : <span className="faint">never run</span> },
                { label: "Last run", render: (x) => x.last_run_at ? when(x.last_run_at) : "—" }, { label: "Total runs", render: (x) => num(x.run_count) },
              ]} />
            </Card>
          </div>
        );
      }}
    </Loading>
  );
}
