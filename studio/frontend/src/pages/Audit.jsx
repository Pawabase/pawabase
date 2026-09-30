import Layout from "../components/Layout";
import { Card, PageHead, Table, when } from "../components/ui";

export default function Audit({ entries, org }) {
  return (
    <Layout title="Audit log">
      <PageHead title="Audit log" description={`Every change made in ${org.name}, and by whom.`} />
      <Card flush>
        <Table
          rows={entries}
          columns={[
            { label: "When", render: (r) => <span title={r.created_at}>{when(r.created_at)}</span> },
            { label: "Actor", key: "actor" },
            { label: "Action", render: (r) => <code>{r.action}</code> },
            { label: "Where", render: (r) => (r.project ? `${r.project}/${r.env || "*"}` : "organization") },
            { label: "Target", key: "target" },
          ]}
        />
      </Card>
    </Layout>
  );
}
