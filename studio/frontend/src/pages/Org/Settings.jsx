import { router } from "@inertiajs/react";
import { useState } from "react";
import Layout, { ORG_ROLE_LABELS, atLeast } from "../../components/Layout";
import { Button, Card, Field, PageHead, useAction } from "../../components/ui";
import { del, patch } from "../../lib/api";

export default function OrgSettings({ org }) {
  const [name, setName] = useState(org.name);
  const [run, busy] = useAction();
  const owner = atLeast(org.role, "owner");
  return (
    <Layout title="Organization settings">
      <PageHead title="Organization settings" description={`You are ${ORG_ROLE_LABELS[org.role]?.toLowerCase()} of ${org.name}.`} />
      <div className="stack" style={{ gap: 20, maxWidth: 640 }}>
        <Card title="General">
          <div className="stack" style={{ gap: 14 }}>
            <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="URL name" hint="Fixed once created."><input className="mono" value={org.slug} disabled /></Field>
            <div><Button variant="primary" disabled={busy || !name.trim() || name === org.name} onClick={async () => {
              if (await run(() => patch(`/orgs/${org.slug}`, { name: name.trim() }), "Saved")) router.reload();
            }}>Save</Button></div>
          </div>
        </Card>
        <Card title="Danger zone">
          <div className="spread">
            <span className="muted">
              {owner
                ? `Deleting ${org.name} removes its team. It must have no projects: delete or export them first.`
                : "Only owners can delete an organization."}
            </span>
            <Button variant="danger" disabled={!owner || busy} onClick={async () => {
              if (prompt(`Type ${org.slug} to delete the organization`) !== org.slug) return;
              if (await run(() => del(`/orgs/${org.slug}`), "Organization deleted")) router.visit("/");
            }}>Delete organization</Button>
          </div>
        </Card>
      </div>
    </Layout>
  );
}
