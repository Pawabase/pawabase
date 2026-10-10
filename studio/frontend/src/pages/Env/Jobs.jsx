import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Json, Loading, Modal, PageHead, Status, Table, Tabs, useAction, when } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";

export default function Jobs({ env }) {
  const base = envPath(env);
  const [tab, setTab] = useState("jobs");
  const [status, setStatus] = useState("");
  const jobs = useApi(`${base}/jobs`, { params: { status, limit: 100 }, interval: 5000 });
  const failed = useApi(tab === "failed" ? `${base}/failed-jobs` : null);
  const workers = useApi(tab === "workers" ? "/workers" : null, { interval: 10000 });
  const [job, setJob] = useState(null);
  const [run] = useAction();
  const open = async (id) => setJob(await get(`${base}/jobs/${id}`));
  return (
    <Layout title="Jobs & queues">
      <PageHead title="Jobs & queues" description="Background work on Sillo's queue: flow runs, function calls, webhook deliveries, mail. Failed jobs retry with backoff, then land here." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "jobs", label: "Jobs" }, { value: "failed", label: "Failed" }, { value: "workers", label: "Workers" }]} />
      {tab === "jobs" && (
        <Card flush title={<select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 160 }}><option value="">every status</option>{["queued", "active", "retrying", "succeeded", "failed"].map((s) => <option key={s}>{s}</option>)}</select>}>
          <Loading state={jobs} empty="No jobs.">
            {(data) => <Table rows={data.data} onRowClick={(j) => open(j.id)} columns={[
              { label: "Job", render: (j) => <code>{j.job}</code> }, { label: "Queue", key: "queue" }, { label: "Status", render: (j) => <Status value={j.status} /> },
              { label: "Attempts", key: "attempts" }, { label: "Source", key: "source" }, { label: "Error", render: (j) => j.error && <span className="error-text">{j.error.slice(0, 80)}</span> }, { label: "Created", render: (j) => when(j.created_at) },
            ]} />}
          </Loading>
        </Card>
      )}
      {tab === "failed" && (
        <Card flush>
          <Loading state={failed} empty="Nothing has failed permanently.">
            {(data) => <Table rows={data.data} onRowClick={(f) => f.job_id && open(f.job_id)} columns={[{ label: "Job", render: (f) => <code>{f.job_name || f.job}</code> }, { label: "Queue", key: "queue" }, { label: "Error", render: (f) => <span className="error-text">{String(f.error || f.exception || "").slice(0, 120)}</span> }, { label: "Failed", render: (f) => when(f.failed_at || f.created_at) }]} />}
          </Loading>
        </Card>
      )}
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
