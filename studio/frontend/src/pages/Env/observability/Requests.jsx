import { useState } from "react";
import { Badge, Card, Loading, Table, when } from "../../../components/ui";
import { useApi } from "../../../lib/api";
import { ms, statusTone } from "./shared";

export default function Requests({ base, onTrace }) {
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [requestId, setRequestId] = useState("");
  const [slow, setSlow] = useState(false);
  const requests = useApi(`${base}/requests`, { params: { status, search, request_id: requestId, limit: 100 }, interval: 10000 });
  return (
    <Card flush title={<div className="row wrap">
      <input placeholder="Search path" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 220 }} />
      <input placeholder="Exact request id" value={requestId} onChange={(e) => setRequestId(e.target.value)} style={{ width: 250 }} />
      <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 150 }}><option value="">every status</option><option value="2xx">2xx success</option><option value="4xx">4xx client error</option><option value="5xx">5xx server error</option></select>
      <label className="row"><input type="checkbox" checked={slow} onChange={(e) => setSlow(e.target.checked)} /> slow only (500 ms+)</label>
    </div>}>
      <Loading state={requests}>
        {(data) => <Table rows={(data.data || []).filter((r) => !slow || r.duration_ms >= 500)} onRowClick={(r) => onTrace(r.request_id)} columns={[
          { label: "When", render: (r) => when(r.started_at) },
          { label: "Method", render: (r) => <Badge tone="blue">{r.method}</Badge> },
          { label: "Path", render: (r) => <code>{r.path}</code> },
          { label: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
          { label: "Duration", render: (r) => ms(r.duration_ms) },
          { label: "Steps", render: (r) => r.notes?.spans?.length ?? <span className="faint">—</span> },
          { label: "Caller", render: (r) => r.user || r.role || "anonymous" },
          { label: "Request id", render: (r) => <code className="faint">{r.request_id}</code> },
        ]} />}
      </Loading>
    </Card>
  );
}
