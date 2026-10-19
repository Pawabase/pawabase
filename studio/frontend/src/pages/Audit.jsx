import { useState } from "react";
import Layout from "../components/Layout";
import { Button, Card, Loading, PageHead, Table, when } from "../components/ui";
import { useApi } from "../lib/api";

/** The management audit log: filter by service, actor or environment, and download what matches. */
export default function Audit({ entries }) {
  const [filters, setFilters] = useState({ action: "", actor: "", env: "" });
  const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
  const filtered = Object.keys(params).length > 0;
  const live = useApi(filtered ? "/audit" : null, { params: { ...params, limit: 200 } });
  const rows = filtered ? live.data?.data : entries;
  const set = (key) => (e) => setFilters({ ...filters, [key]: e.target.value.trim() });
  const exportUrl = (fmt) => `/studio/api/platform/audit/export?${new URLSearchParams({ ...params, format: fmt })}`;
  return (
    <Layout title="Audit log">
      <PageHead title="Audit log" description="Every change made through Studio, the API and the CLI, and who made it. Kept for the installation's audit retention (a year by default)."
        actions={<><Button onClick={() => window.open(exportUrl("csv"))}>Export CSV</Button><Button onClick={() => window.open(exportUrl("jsonl"))}>Export JSON lines</Button></>} />
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ marginBottom: 12 }}>
        <input placeholder="Action, e.g. webhook.*" defaultValue="" onChange={set("action")} style={{ width: 220 }} />
        <input placeholder="Actor" onChange={set("actor")} style={{ width: 180 }} />
        <input placeholder="Environment" onChange={set("env")} style={{ width: 160 }} />
      </div>
      <Card flush>
        <Loading state={filtered ? live : { data: true }}>
          {() => (
            <Table
              rows={rows || []}
              empty="Nothing matches."
              columns={[
                { label: "When", render: (r) => <span title={r.created_at}>{when(r.created_at)}</span> },
                { label: "Actor", render: (r) => r.actor || <span className="faint text-faint">—</span> },
                { label: "Action", render: (r) => <code>{r.action}</code> },
                { label: "Environment", render: (r) => r.env || "—" },
                { label: "Target", key: "target" },
              ]}
            />
          )}
        </Loading>
      </Card>
    </Layout>
  );
}
