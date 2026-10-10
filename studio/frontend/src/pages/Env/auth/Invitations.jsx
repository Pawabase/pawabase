import { useState } from "react";
import { Icon } from "../../../components/icons";
import { Badge, Button, Card, Field, Loading, Modal, Table, TagInput, useAction, when } from "../../../components/ui";
import { del, post, useApi } from "../../../lib/api";
import { AUTH } from "./shared";

const ROLES = ["member", "admin", "viewer", "owner"];
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Invite one or more people to an organization. Each gets an email with a link that is good for seven days. */
export function InviteSheet({ base, org, onClose, onSent }) {
  const orgs = useApi(org ? null : `${base}/orgs`, AUTH);
  const [slug, setSlug] = useState(org || "");
  const [emails, setEmails] = useState([]);
  const [role, setRole] = useState("member");
  const [redirect, setRedirect] = useState("");
  const [results, setResults] = useState(null);
  const [run, busy] = useAction();
  const list = orgs.data?.data || [];
  const chosen = org || slug || list[0]?.slug || "";
  const bad = emails.filter((email) => !EMAIL.test(email));
  const send = async () => {
    const outcome = await run(async () => {
      const done = [];
      for (const email of emails) {
        try {
          await post(`${base}/orgs/${chosen}/invitations`, { email, role, redirect_to: redirect.trim() || null }, AUTH);
          done.push({ email, ok: true });
        } catch (error) {
          done.push({ email, ok: false, why: error.message });
        }
      }
      return done;
    });
    if (outcome && outcome !== true) { setResults(outcome); onSent(); }
  };
  return (
    <Modal title="Invite people" onClose={onClose} footer={results ? <Button variant="primary" onClick={onClose}>Done</Button> : <Button variant="primary" disabled={busy || !chosen || !emails.length || bad.length > 0} onClick={send}>{emails.length > 1 ? `Send ${emails.length} invitations` : "Send invitation"}</Button>}>
      {results ? (
        <div className="stack">
          {results.map((r) => (
            <div key={r.email} className={`alert ${r.ok ? "ok" : "error"}`}><b>{r.email}</b> {r.ok ? "was sent an invitation." : `could not be invited: ${r.why}`}</div>
          ))}
        </div>
      ) : (
        <>
          {!org && (
            <Field label="Organization">
              <Loading state={orgs} empty="Create an organization first.">
                {() => <select value={chosen} onChange={(e) => setSlug(e.target.value)}>{list.map((o) => <option key={o.slug} value={o.slug}>{o.name} ({o.slug})</option>)}</select>}
              </Loading>
            </Field>
          )}
          <Field label="Email addresses" hint="Type an address and press Enter. Add as many as you like.">
            <TagInput value={emails} onChange={(v) => setEmails(v.map((e) => e.trim().toLowerCase()))} placeholder="ada@example.com" />
            {bad.length > 0 && <span className="error-text">{bad.join(", ")} {bad.length === 1 ? "is" : "are"} not a valid email address.</span>}
          </Field>
          <Field label="Role"><select value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></Field>
          <Field label="Where the link goes" optional hint="An address from Domains where your application finishes the invitation. Leave empty to use none.">
            <input value={redirect} onChange={(e) => setRedirect(e.target.value)} placeholder="https://app.example.com/accept-invite" />
          </Field>
          <p className="hint" style={{ margin: 0 }}>The person needs an account with the same email address to accept. Your application reads the token from the link and calls the accept endpoint.</p>
        </>
      )}
    </Modal>
  );
}

/** Invitations that have been sent and not yet accepted, across every organization. */
export default function Invitations({ base }) {
  const invitations = useApi(`${base}/invitations`, AUTH);
  const [inviting, setInviting] = useState(false);
  const [run, busy] = useAction();
  const revoke = async (row) => { if (confirm(`Revoke the invitation for ${row.email}?`) && await run(() => del(`${base}/invitations/${row.id}`, AUTH), "Invitation revoked")) invitations.reload(); };
  const resend = async (row) => { if (await run(() => post(`${base}/invitations/${row.id}/resend`, {}, AUTH), `Sent again to ${row.email}`)) invitations.reload(); };
  return (
    <>
      <Card flush title="Pending invitations" actions={<Button variant="primary" size="sm" onClick={() => setInviting(true)}><Icon name="plus" size={14} /> Invite people</Button>}>
        <Loading state={invitations} empty="No pending invitations.">
          {(data) => (
            <Table
              rows={data.data}
              empty="No pending invitations. Invite people with the button above."
              columns={[
                { label: "Email", render: (i) => <b>{i.email}</b> },
                { label: "Organization", render: (i) => <span>{i.org_name} <code className="faint">{i.org}</code></span> },
                { label: "Role", render: (i) => <Badge>{i.role}</Badge> },
                { label: "Sent", render: (i) => when(i.created_at) },
                { label: "Expires", render: (i) => when(i.expires_at) },
                { label: "", render: (i) => <span className="row" style={{ gap: 6 }}><Button size="sm" disabled={busy} onClick={() => resend(i)}>Resend</Button><Button size="sm" variant="danger" disabled={busy} onClick={() => revoke(i)}>Revoke</Button></span> },
              ]}
            />
          )}
        </Loading>
      </Card>
      {inviting && <InviteSheet base={base} onClose={() => setInviting(false)} onSent={invitations.reload} />}
    </>
  );
}
