import { Link, router } from "@inertiajs/react";
import Layout from "../../components/Layout";
import { Badge, Card, Loading, PageHead, Status, Table, when } from "../../components/ui";
import { envPath, useApi } from "../../lib/api";

export default function Flows({ project, env }) {
  const flows = useApi(envPath(project.ref, env, "/flows"));
  const runs = useApi(envPath(project.ref, env, "/flow-runs"), { params: { limit: 15 }, interval: 10000 });
  const editor = (name) => `/projects/${project.ref}/${env}/flows/${name}`;
  return (
    <Layout title="Flows">
      <PageHead
        title="Flows"
        description="Visual workflows built from blocks. Triggered by routes, events, schedules, webhooks, realtime messages or other flows."
        actions={<Link className="btn primary" href={editor("new")}>New flow</Link>}
      />
      <div className="stack lg">
        <Card flush>
          <Loading state={flows} empty="No flows yet. Create one to start automating.">
            {(data) => (
              <Table
                rows={data.data}
                onRowClick={(f) => router.visit(editor(f.name))}
                columns={[
                  { label: "Name", render: (f) => <b>{f.name}</b> },
                  { label: "Description", key: "description" },
                  { label: "Triggers", render: (f) => <div className="row wrap">{triggers(f).map((t) => <Badge key={t} tone="blue">{t}</Badge>)}</div> },
                  { label: "Blocks", render: (f) => f.definition?.nodes?.length ?? 0 },
                  { label: "State", render: (f) => <Badge tone={f.enabled ? "green" : ""}>{f.enabled ? "enabled" : "disabled"}</Badge> },
                ]}
              />
            )}
          </Loading>
        </Card>
        <Card flush title="Recent runs">
          <Loading state={runs} empty="No runs yet.">
            {(data) => (
              <Table
                rows={data.data}
                onRowClick={(r) => router.visit(editor(r.flow))}
                columns={[
                  { label: "Flow", key: "flow" },
                  { label: "Status", render: (r) => <Status value={r.status} /> },
                  { label: "Trigger", key: "trigger" },
                  { label: "Duration", render: (r) => (r.duration_ms != null ? `${Math.round(r.duration_ms)} ms` : "—") },
                  { label: "Error", render: (r) => r.error ? <span className="error-text">{r.error}</span> : "" },
                  { label: "When", render: (r) => when(r.created_at) },
                ]}
              />
            )}
          </Loading>
        </Card>
      </div>
    </Layout>
  );
}

function triggers(flow) {
  return (flow.definition?.nodes || []).map((n) => n.data?.block).filter((b) => b?.startsWith("trigger.")).map((b) => b.slice(8));
}
