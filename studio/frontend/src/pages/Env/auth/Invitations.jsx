import { Badge, Button, Card, Loading, Table, useAction, when } from "../../../components/ui";
import { del, useApi } from "../../../lib/api";
import { AUTH } from "./shared";

/** Invitations that have been sent and not yet accepted, across every organization. */
export default function Invitations({ base }) {
  const invitations = useApi(`${base}/invitations`, AUTH);
  const [run, busy] = useAction();
  const revoke = async (row) => { if (confirm(`Revoke the invitation for ${row.email}?`) && await run(() => del(`${base}/invitations/${row.id}`, AUTH), "Invitation revoked")) invitations.reload(); };
  return (
    <Card flush title="Pending invitations" actions={<span className="muted small">Organization members invite people from your application.</span>}>
      <Loading state={invitations} empty="No pending invitations.">
        {(data) => (
          <Table
            rows={data.data}
            empty="No pending invitations."
            columns={[
              { label: "Email", render: (i) => <b>{i.email}</b> },
              { label: "Organization", render: (i) => <span>{i.org_name} <code className="faint">{i.org}</code></span> },
              { label: "Role", render: (i) => <Badge>{i.role}</Badge> },
              { label: "Sent", render: (i) => when(i.created_at) },
              { label: "Expires", render: (i) => when(i.expires_at) },
              { label: "", render: (i) => <Button size="sm" variant="danger" disabled={busy} onClick={() => revoke(i)}>Revoke</Button> },
            ]}
          />
        )}
      </Loading>
    </Card>
  );
}
