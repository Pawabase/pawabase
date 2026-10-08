import { Badge, Card, Loading, Table, when } from "../../../components/ui";
import JobDetail from "./JobDetail";
import { Stat, StatStrip } from "./kit";
import { Link } from "@inertiajs/react";
import { envHref } from "../../../components/Layout";
import { useState } from "react";
import { envPath, get } from "../../../lib/api";
import { COLOR, RateBadge, SeriesCard, num, span } from "./system";
import { ms } from "./shared";

export default function Queues({ env, system }) {
  const base = envPath(env);
  const [job, setJob] = useState(null);
  const open = async (id) => setJob(await get(`${base}/jobs/${id}`));
  return (
    <Loading state={system}>
      {(s) => {
        const b = s.jobs.backlog;
        return (
          <div className="stack lg">
            <StatStrip>
              <Stat tone={b.waiting ? "butter" : "mint"} icon="jobs" value={num(b.waiting)} label="waiting to run" />
              <Stat tone={b.oldest_seconds > 300 ? "rose" : "mint"} icon="clock" value={b.waiting ? span(b.oldest_seconds) : "—"} label="oldest waiting" />
              <Stat tone={b.stuck.length ? "rose" : "mint"} icon="pulse" value={b.stuck.length} label="running over 15 min" />
              <Stat tone="sky" icon="check" value={num(s.jobs.totals.total)} label="jobs in this window" />
            </StatStrip>
            <SeriesCard title="Throughput" window={s.window} data={s.jobs.series} height={220}
              lines={[["succeeded", "Succeeded", COLOR.ok], ["failed", "Failed", COLOR.bad, "line"], ["retrying", "Retrying", COLOR.warn, "line"], ["queued", "Enqueued", COLOR.info, "line"]]} />
            {s.jobs.truncated && <div className="alert info">This window has more jobs than are summarised here; figures cover the newest 20,000.</div>}
            <Card flush title="Queues" actions={<Link className="link-more" href={envHref(env, "jobs")}>Manage jobs</Link>}>
              <Table rows={s.jobs.queues} empty="No queues yet." columns={[
                { label: "Queue", render: (q) => <code>{q.queue}</code> },
                { label: "Waiting", render: (q) => q.depth ? <Badge tone="yellow">{q.depth}</Badge> : <span className="faint">0</span> },
                { label: "Jobs", key: "total" },
                { label: "Failed", render: (q) => q.failed ? <span className="error-text">{q.failed}</span> : 0 },
                { label: "Success", render: (q) => <RateBadge value={q.success_rate} /> },
                { label: "Avg", render: (q) => q.avg_ms != null ? ms(q.avg_ms) : "—" },
                { label: "p95", render: (q) => q.p95_ms != null ? ms(q.p95_ms) : "—" },
              ]} />
            </Card>
            <Card flush title="Slowest and busiest job types">
              <Table rows={s.jobs.by_job} empty="No jobs in this window." columns={[
                { label: "Job", render: (j) => <code>{j.job}</code> }, { label: "Runs", key: "total" },
                { label: "Failed", render: (j) => j.failed ? <span className="error-text">{j.failed}</span> : 0 },
                { label: "Avg", render: (j) => j.avg_ms != null ? ms(j.avg_ms) : "—" }, { label: "p95", render: (j) => j.p95_ms != null ? ms(j.p95_ms) : "—" },
                { label: "Slowest", render: (j) => j.max_ms != null ? ms(j.max_ms) : "—" },
              ]} />
            </Card>
            {b.stuck.length > 0 && <Card flush title="Possibly stuck">
              <Table rows={b.stuck} onRowClick={(j) => open(j.id)} columns={[{ label: "Job", render: (j) => <code>{j.job}</code> }, { label: "Running for", render: (j) => span(j.running_seconds) }, { label: "Id", render: (j) => <code className="faint">{j.id}</code> }]} />
            </Card>}
            <Card flush title="Recent failures">
              <Table rows={s.jobs.recent_failures} empty="Nothing failed in this window." onRowClick={(j) => open(j.id)} columns={[
                { label: "When", render: (j) => when(j.at) }, { label: "Job", render: (j) => <code>{j.job}</code> }, { label: "Queue", key: "queue" },
                { label: "Times", render: (j) => j.count > 1 ? <Badge tone="red">×{j.count}</Badge> : 1 }, { label: "Error", render: (j) => <span className="error-text">{j.error.slice(0, 120)}</span> },
              ]} />
            </Card>
            {job && <JobDetail base={base} job={job} onClose={() => setJob(null)} />}
          </div>
        );
      }}
    </Loading>
  );
}
