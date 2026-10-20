import { useState } from "react";
import Layout, { sectionNav, useSection } from "../../components/Layout";
import { Badge, Button, Card, Json, Loading, Modal, PageHead, Status, Table, useAction, when } from "../../components/ui";
import { del, envPath, get, post, useApi } from "../../lib/api";

export default function Jobs({ env }) {
  const base = envPath(env);
  const [tab, setTab] = useSection(["jobs", "failed", "health", "retries", "workers"]);
  const [status, setStatus] = useState("");
  const jobs = useApi(`${base}/jobs`, { params: { status, limit: 100 }, interval: 5000 });
  const failed = useApi(tab === "failed" ? `${base}/failed-jobs` : null);
  const workers = useApi(tab === "workers" ? "/workers" : null, { interval: 10000 });
  const [job, setJob] = useState(null);
  const [run, busy] = useAction();
  const replayOne = async (id) => { if (await run(() => post(`${base}/jobs/${id}/retry`), "Queued again")) { jobs.reload(); failed.reload?.(); } };
  /** Count what a filter would replay, ask, then do it. */
  const replayAll = async (filter, label) => {
    const look = await run(() => post(`${base}/jobs/replay`, { ...filter, dry_run: true }));
    if (!look || look === true) return;
    if (!look.matched) { alert("No jobs match."); return; }
    const note = look.skipped.length ? ` ${look.skipped.length} cannot be replayed because their payload was not kept.` : "";
    if (!confirm(`Queue ${look.queued} ${label} job${look.queued === 1 ? "" : "s"} again?${note}`)) return;
    if (await run(() => post(`${base}/jobs/replay`, filter), "Queued again")) { jobs.reload(); failed.reload?.(); }
  };
  const open = async (id) => setJob(await get(`${base}/jobs/${id}`));
  return (
    <Layout title="Jobs & queues" subnav={sectionNav("Jobs & queues", [
      { title: "Work", items: [["jobs", "Jobs", null, { icon: "jobs" }], ["failed", "Failed", null, { icon: "x" }]] },
      { title: "Health", items: [["health", "Service health", null, { icon: "observability" }], ["retries", "Retry policies", null, { icon: "refresh" }]] },
      { title: "Capacity", items: [["workers", "Workers", null, { icon: "terminal" }]] },
    ], tab, setTab)}>
      <PageHead title="Jobs & queues" description="Background work on Sillo's queue: flow runs, function calls, webhook deliveries, mail. Failed jobs retry with backoff, then land here." />
      {tab === "jobs" && (
        <Card icon="search" flush actions={<Button size="sm" disabled={busy} onClick={() => replayAll({ status: status || "failed", limit: 500 }, status || "failed")}>Replay all {status || "failed"}</Button>} title={<select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 160 }}><option value="">every status</option>{["queued", "active", "retrying", "succeeded", "failed"].map((s) => <option key={s}>{s}</option>)}</select>}>
          <Loading state={jobs} empty="No jobs.">
            {(data) => <Table rows={data.data} onRowClick={(j) => open(j.id)} columns={[
              { label: "Job", render: (j) => <code>{j.job}</code> }, { label: "Queue", key: "queue" }, { label: "Status", render: (j) => <Status value={j.status} /> },
              { label: "Attempts", key: "attempts" }, { label: "Source", key: "source" }, { label: "Error", render: (j) => j.error && <span className="error-text text-danger text-ui-sm font-medium">{j.error.slice(0, 80)}</span> }, { label: "Created", render: (j) => when(j.created_at) }, { label: "", render: (j) => <Button size="sm" onClick={(e) => { e.stopPropagation(); replayOne(j.id); }}>Replay</Button> },
            ]} />}
          </Loading>
        </Card>
      )}
      {tab === "failed" && (
        <Card icon="status" flush title="Failed for good" actions={<Button size="sm" disabled={busy} onClick={() => replayAll({ status: "failed", limit: 500 }, "failed")}>Replay all failed</Button>}>
          <Loading state={failed} empty="Nothing has failed permanently.">
            {(data) => <Table rows={data.data} onRowClick={(f) => f.job_id && open(f.job_id)} columns={[{ label: "Job", render: (f) => <code>{f.job_name || f.job}</code> }, { label: "Queue", key: "queue" }, { label: "Error", render: (f) => <span className="error-text text-danger text-ui-sm font-medium">{String(f.error || f.exception || "").slice(0, 120)}</span> }, { label: "Failed", render: (f) => when(f.failed_at || f.created_at) }, { label: "", render: (f) => f.job_id && <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}><Button size="sm" onClick={(e) => { e.stopPropagation(); replayOne(f.job_id); }}>Replay</Button><Button size="sm" onClick={async (e) => { e.stopPropagation(); if (await run(() => del(`${base}/failed-jobs/${f.job_id}`), "Dismissed")) failed.reload(); }}>Dismiss</Button></span> }]} />}
          </Loading>
        </Card>
      )}
      {tab === "health" && <ServiceHealth base={base} />}
      {tab === "retries" && <RetryPolicies />}
      {tab === "workers" && (
        <Card flush>
          <Loading state={workers} empty="No worker has reported in. Start one with `python -m app.worker`.">
            {(data) => <Table rows={data.data} columns={[{ label: "Worker", key: "name" }, { label: "Kind", key: "kind" }, { label: "Host", key: "host" }, { label: "Queues", key: "queues" }, { label: "Last seen", render: (w) => when(w.last_seen_at || w.updated_at) }]} />}
          </Loading>
        </Card>
      )}
      {job && (
        <Modal wide title={`Job ${job.job}`} onClose={() => setJob(null)} footer={["failed", "retrying"].includes(job.status) && <Button variant="primary" onClick={async () => { if (await run(() => post(`${base}/jobs/${job.id}/retry`), "Retry queued")) { setJob(null); jobs.reload(); } }}>Retry</Button>}>
          <Json value={job} />
        </Modal>
      )}
    </Layout>
  );
}

const fmtWait = (seconds) => (!seconds ? "—" : seconds < 120 ? `${seconds}s` : seconds < 7200 ? `${Math.round(seconds / 60)}m` : `${Math.round(seconds / 3600)}h`);

function ServiceHealth({ base }) {
  const [minutes, setMinutes] = useState(60);
  const health = useApi(`${base}/service-health`, { params: { minutes }, interval: 10000 });
  return (
    <Card icon="terminal" flush title="Every service, the last" actions={<select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} style={{ width: 140 }}>{[[15, "15 minutes"], [60, "hour"], [1440, "day"], [10080, "week"]].map(([m, l]) => <option key={m} value={m}>{l}</option>)}</select>}>
      <Loading state={health}>
        {(data) => <Table rows={data.data} columns={[
          { label: "Service", render: (s) => <b>{s.label}</b> },
          { label: "Done", key: "total" },
          { label: "Failed", render: (s) => (s.failed ? <Badge tone="red">{s.failed}</Badge> : "0") },
          { label: "Failure rate", render: (s) => <span className={s.failure_rate >= 10 ? "danger-text" : ""}>{s.failure_rate}%</span> },
          { label: "Retrying", key: "retrying" },
          { label: "Waiting", key: "waiting" },
          { label: "Oldest waiting", render: (s) => <span className={s.oldest_waiting_seconds > 600 ? "danger-text" : ""}>{fmtWait(s.oldest_waiting_seconds)}</span> },
        ]} />}
      </Loading>
    </Card>
  );
}

function RetryPolicies() {
  const policies = useApi("/retry-policies");
  return (
    <Card icon="refresh" flush title="How failed work is retried" actions={<span className="hint text-xs font-book text-muted leading-[1.45]">Waits are randomised by ±20%. A webhook endpoint's own attempt limit replaces the default.</span>}>
      <Loading state={policies}>
        {(data) => <Table rows={data.data} columns={[
          { label: "Work", render: (p) => <b>{p.job.replace(/Job$/, "").replace(/([a-z])([A-Z])/g, "$1 $2")}</b> },
          { label: "Queue", render: (p) => <code>{p.queue}</code> },
          { label: "Attempts", key: "max_attempts" },
          { label: "Waits before each retry", render: (p) => (p.waits_seconds.length ? p.waits_seconds.map(fmtWait).join(" → ") : "No retries") },
          { label: "Gives up after", render: (p) => fmtWait(p.total_seconds) },
        ]} />}
      </Loading>
    </Card>
  );
}
