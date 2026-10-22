// Background jobs: what ran for your app behind the scenes (function runs, flow runs, webhook deliveries, mail, events),
// what failed, and how failed work is retried. Replay anything that failed.

import { useState } from "react";
import { Button, Card, Json, Loading, Modal, Segmented, Status, Table, useAction, when } from "../../../components/ui";
import { del, envPath, get, post, useApi } from "../../../lib/api";
import { COLOR, SeriesCard } from "./system";

/** Job classes are generic (one class runs every function, flow and webhook); the row's target is the real name. */
const JOB_KINDS = { RunFunctionJob: "function run", RunFlowJob: "flow run", DeliverWebhookJob: "webhook delivery", SendMailJob: "mail", ProcessEventJob: "event", PurgeEnvironmentJob: "environment purge" };
const shortClass = (name) => String(name || "").split(/[.:]/).pop();
const jobKind = (row) => JOB_KINDS[shortClass(row.job || row.job_class)] || shortClass(row.job || row.job_class) || "job";
const jobTarget = (row) => row.target || row.payload?.function || row.payload?.flow || row.payload?.url || "";
/** Failed rows keep the payload as text; the name lives in its kwargs. */
const failedTarget = (row) => {
  if (row.target) return row.target;
  try {
    const args = JSON.parse(row.payload || "{}").kwargs || {};
    return args.function || args.flow || args.url || "";
  } catch { return ""; }
};
const fmtWait = (seconds) => (!seconds ? "—" : seconds < 120 ? `${seconds}s` : seconds < 7200 ? `${Math.round(seconds / 60)}m` : `${Math.round(seconds / 3600)}h`);

function JobName({ subject, kind }) {
  return <span className="flex items-baseline gap-1.5 min-w-0"><code className="truncate max-w-[300px]">{subject || kind}</code>{subject && <span className="text-faint text-ui-sm shrink-0">{kind}</span>}</span>;
}

const VIEWS = [["jobs", "All jobs"], ["failed", "Failed"], ["retries", "Retry rules"]];
const STATUSES = ["queued", "active", "retrying", "succeeded", "failed"];

export default function JobsPanel({ env, system }) {
  const base = envPath(env);
  const [view, setView] = useState("jobs");
  const [status, setStatus] = useState("");
  const [job, setJob] = useState(null);
  const [run, busy] = useAction();
  const jobs = useApi(view === "jobs" ? `${base}/jobs` : null, { params: { status, limit: 100 }, interval: 5000 });
  const failed = useApi(view === "failed" ? `${base}/failed-jobs` : null);
  const reload = () => { jobs.reload?.(); failed.reload?.(); };
  const open = async (id) => setJob(await get(`${base}/jobs/${id}`));
  const replayOne = async (id) => { if (await run(() => post(`${base}/jobs/${id}/retry`), "Queued again")) reload(); };
  /** Count what a filter would replay, ask, then do it. */
  const replayAll = async (filter, label) => {
    const look = await run(() => post(`${base}/jobs/replay`, { ...filter, dry_run: true }));
    if (!look || look === true) return;
    if (!look.matched) { alert("No jobs match."); return; }
    const note = look.skipped.length ? ` ${look.skipped.length} cannot be replayed because their data was not kept.` : "";
    if (!confirm(`Run ${look.queued} ${label} job${look.queued === 1 ? "" : "s"} again?${note}`)) return;
    if (await run(() => post(`${base}/jobs/replay`, filter), "Queued again")) reload();
  };

  return (
    <div className="stack lg">
      <div className="row"><Segmented value={view} onChange={setView} options={VIEWS} /></div>

      {view === "jobs" && (
        <>
          {system.data && (
            <SeriesCard title="Jobs over time" window={system.data.window} data={system.data.jobs.series} height={200}
              lines={[["succeeded", "Succeeded", COLOR.ok], ["failed", "Failed", COLOR.bad, "line"], ["retrying", "Retrying", COLOR.warn, "line"]]} />
          )}
          <Card bleed icon="jobs" flush
            title={<select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 160 }} aria-label="Status"><option value="">Every status</option>{STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}</select>}
            actions={<Button size="sm" disabled={busy} onClick={() => replayAll({ status: status || "failed", limit: 500 }, status || "failed")}>Run all {status || "failed"} again</Button>}>
            <Loading state={jobs} empty="No jobs yet.">
              {(data) => <Table rows={data.data} onRowClick={(j) => open(j.id)} columns={[
                { label: "Job", render: (j) => <JobName subject={jobTarget(j)} kind={jobKind(j)} /> },
                { label: "Status", render: (j) => <Status value={j.status} /> },
                { label: "Tries", key: "attempts" },
                { label: "Error", render: (j) => j.error && <span className="error-text text-danger text-ui-sm font-medium">{j.error.slice(0, 80)}</span> },
                { label: "When", render: (j) => when(j.created_at) },
                { label: "", render: (j) => <Button size="sm" onClick={(e) => { e.stopPropagation(); replayOne(j.id); }}>Run again</Button> },
              ]} />}
            </Loading>
          </Card>
        </>
      )}

      {view === "failed" && (
        <Card bleed icon="status" flush title="Gave up after retrying" actions={<Button size="sm" disabled={busy} onClick={() => replayAll({ status: "failed", limit: 500 }, "failed")}>Run all again</Button>}>
          <Loading state={failed} empty="Nothing has failed for good.">
            {(data) => <Table rows={data.data} onRowClick={(f) => f.job_id && open(f.job_id)} columns={[
              { label: "Job", render: (f) => <JobName subject={failedTarget(f)} kind={jobKind(f)} /> },
              { label: "Error", render: (f) => <span className="error-text text-danger text-ui-sm font-medium">{String(f.error || f.exception || "").slice(0, 120)}</span> },
              { label: "When", render: (f) => when(f.failed_at || f.created_at) },
              { label: "", render: (f) => f.job_id && (
                <span className="row" style={{ gap: 6 }}>
                  <Button size="sm" onClick={(e) => { e.stopPropagation(); replayOne(f.job_id); }}>Run again</Button>
                  <Button size="sm" onClick={async (e) => { e.stopPropagation(); if (await run(() => del(`${base}/failed-jobs/${f.job_id}`), "Dismissed")) failed.reload(); }}>Dismiss</Button>
                </span>
              ) },
            ]} />}
          </Loading>
        </Card>
      )}

      {view === "retries" && <RetryRules />}

      {job && (
        <Modal wide title={`${jobTarget(job) || jobKind(job)} · ${jobKind(job)}`} onClose={() => setJob(null)}
          footer={["failed", "retrying"].includes(job.status) && <Button variant="primary" onClick={async () => { if (await run(() => post(`${base}/jobs/${job.id}/retry`), "Retry queued")) { setJob(null); reload(); } }}>Retry</Button>}>
          <Json value={job} />
        </Modal>
      )}
    </div>
  );
}

function RetryRules() {
  const policies = useApi("/retry-policies");
  return (
    <Card bleed icon="refresh" flush title="How failed work is retried" actions={<span className="hint">Waits vary by about 20%. A webhook endpoint can set its own attempts.</span>}>
      <Loading state={policies}>
        {(data) => <Table rows={data.data} columns={[
          { label: "Work", render: (p) => <b>{p.job.replace(/Job$/, "").replace(/([a-z])([A-Z])/g, "$1 $2")}</b> },
          { label: "Tries", key: "max_attempts" },
          { label: "Wait before each retry", render: (p) => (p.waits_seconds.length ? p.waits_seconds.map(fmtWait).join(" → ") : "No retries") },
          { label: "Gives up after", render: (p) => fmtWait(p.total_seconds) },
        ]} />}
      </Loading>
    </Card>
  );
}
