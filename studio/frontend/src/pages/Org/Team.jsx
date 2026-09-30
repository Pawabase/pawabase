import { router } from "@inertiajs/react";
import { useState } from "react";
import Layout, { ORG_ROLE_LABELS, atLeast } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, CopyText, Field, Modal, PageHead, Table, useAction, when } from "../../components/ui";
import { del, post, put } from "../../lib/api";

const ROLE_HELP = {
  owner: "Everything, including deleting the organization and granting ownership.",
  admin: "Manages people and invitations, creates and deletes projects, and edits the organization.",
  developer: "Creates projects and changes anything inside them: definitions, keys, secrets, data.",
  viewer: "Reads every project in the organization. Changes nothing.",
};
const ROLE_TONE = { owner: "brand", admin: "green", developer: "yellow", viewer: "" };

export default function Team({ org, members, invitations, me }) {
  const [inviting, setInviting] = useState(false);
  const [link, setLink] = useState(null);
  const [run, busy] = useAction();
  const manage = atLeast(org.role, "admin");
  const reload = () => router.reload();
  const roles = org.role === "owner" ? ["viewer", "developer", "admin", "owner"] : ["viewer", "developer", "admin"];

  const setRole = async (member, role) => {
    if (await run(() => put(`/orgs/${org.slug}/members/${member.user_id}`, { role }), "Role updated")) reload();
  };
  const remove = async (member) => {
    const leaving = member.user_id === me;
    if (!confirm(leaving ? `Leave ${org.name}? You will lose access to its projects.` : `Remove ${member.email} from ${org.name}?`)) return;
    if (await run(() => del(`/orgs/${org.slug}/members/${member.user_id}`), leaving ? "You left the organization" : "Member removed")) {
      if (leaving) router.visit("/");
      else reload();
    }
  };
  const revoke = async (invitation) => {
    if (await run(() => del(`/orgs/${org.slug}/invitations/${invitation.id}`), "Invitation revoked")) reload();
  };

  return (
    <Layout title="Team">
      <PageHead
        title="Team"
        description={`The people who can work in ${org.name}. Roles apply to every project in the organization.`}
        actions={manage && <Button variant="primary" onClick={() => setInviting(true)}><Icon name="plus" />Invite someone</Button>}
      />
      <Card flush title={`Members · ${members.length}`}>
        <Table
          rows={members}
          columns={[
            { label: "Member", render: (m) => (
              <span className="row" style={{ gap: 10 }}>
                <span className="avatar">{(m.email || "?").slice(0, 1).toUpperCase()}</span>
                <span className="stack" style={{ gap: 0 }}>
                  <b>{m.name || m.email}{m.user_id === me && <span className="faint"> (you)</span>}</b>
                  {m.name && <span className="faint">{m.email}</span>}
                </span>
              </span>
            ) },
            { label: "Role", render: (m) => (
              manage && (m.role !== "owner" || org.role === "owner") ? (
                <select value={m.role} onChange={(e) => setRole(m, e.target.value)} disabled={busy} style={{ width: "auto", minWidth: 130 }}>
                  {[...new Set([...roles, m.role])].map((r) => <option key={r} value={r}>{ORG_ROLE_LABELS[r]}</option>)}
                </select>
              ) : <Badge tone={ROLE_TONE[m.role]}>{ORG_ROLE_LABELS[m.role]}</Badge>
            ) },
            { label: "Joined", render: (m) => when(m.joined_at) },
            { label: "", render: (m) => (
              (m.user_id === me || manage) && (
                <Button size="sm" variant={m.user_id === me ? undefined : "danger"} disabled={busy} onClick={() => remove(m)}>
                  {m.user_id === me ? "Leave" : "Remove"}
                </Button>
              )
            ) },
          ]}
        />
      </Card>

      {manage && (
        <div style={{ marginTop: 20 }}>
          <Card flush title={`Pending invitations · ${invitations.length}`}>
            <Table
              rows={invitations}
              empty="No open invitations. Invite a teammate by email."
              columns={[
                { label: "Email", render: (i) => <span className="row"><Icon name="mail" size={15} />{i.email}</span> },
                { label: "Role", render: (i) => <Badge tone={ROLE_TONE[i.role]}>{ORG_ROLE_LABELS[i.role]}</Badge> },
                { label: "Invited by", key: "invited_by" },
                { label: "Expires", render: (i) => new Date(i.expires_at).toLocaleDateString() },
                { label: "", render: (i) => <Button size="sm" disabled={busy} onClick={() => revoke(i)}>Revoke</Button> },
              ]}
            />
          </Card>
        </div>
      )}

      <div style={{ marginTop: 20 }}>
        <Card title="What each role can do">
          <dl className="kv">
            {["owner", "admin", "developer", "viewer"].map((r) => (
              <div key={r} style={{ display: "contents" }}><dt><Badge tone={ROLE_TONE[r]}>{ORG_ROLE_LABELS[r]}</Badge></dt><dd className="muted">{ROLE_HELP[r]}</dd></div>
            ))}
          </dl>
        </Card>
      </div>

      {inviting && <Invite org={org} roles={roles} onClose={() => setInviting(false)} onCreated={(invitation) => { setInviting(false); setLink(invitation); reload(); }} />}
      {link && (
        <Modal title="Invitation ready" onClose={() => setLink(null)} footer={<Button variant="primary" onClick={() => setLink(null)}>Done</Button>}>
          <div className="alert warn">Send this link to {link.email}. It is shown once, works for one person only, and expires {new Date(link.expires_at).toLocaleDateString()}.</div>
          <Field label="Invitation link"><CopyText text={`${window.location.origin}/invite/${link.token}`} /></Field>
          <p className="hint" style={{ margin: 0 }}>They will create their account, or sign in, and join {org.name} as {ORG_ROLE_LABELS[link.role]}.</p>
        </Modal>
      )}
    </Layout>
  );
}

function Invite({ org, roles, onClose, onCreated }) {
  const [data, setData] = useState({ email: "", role: "developer" });
  const [run, busy] = useAction();
  return (
    <Modal
      title="Invite someone"
      onClose={onClose}
      footer={<Button variant="primary" disabled={busy || !data.email} onClick={async () => {
        const invitation = await run(() => post(`/orgs/${org.slug}/invitations`, data));
        if (invitation) onCreated(invitation);
      }}>Create invitation</Button>}
    >
      <Field label="Email"><input type="email" autoFocus value={data.email} onChange={(e) => setData({ ...data, email: e.target.value })} placeholder="teammate@company.com" /></Field>
      <Field label="Role" hint={ROLE_HELP[data.role]}>
        <select value={data.role} onChange={(e) => setData({ ...data, role: e.target.value })}>
          {roles.map((r) => <option key={r} value={r}>{ORG_ROLE_LABELS[r]}</option>)}
        </select>
      </Field>
    </Modal>
  );
}
