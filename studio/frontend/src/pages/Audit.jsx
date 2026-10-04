import Layout from "../components/Layout";
import { Card, PageHead, Table, when } from "../components/ui";

export default function Audit({ entries }) {
  return (
    <Layout title="Audit log">
      <PageHead title="Audit log" description="Every change made through Studio, the API and the CLI, and who made it." />
      <Card flush>
        <Table
          rows={entries}
          columns={[
            { label: "When", render: (r) => <span title={r.created_at}>{when(r.created_at)}</span> },
            { label: "Actor", render: (r) => r.actor || <span className="faint">—</span> },
            { label: "Action", render: (r) => <code>{r.action}</code> },
            { label: "Environment", render: (r) => r.env || "—" },
            { label: "Target", key: "target" },
          ]}
        />
      </Card>
    </Layout>
  );
}
