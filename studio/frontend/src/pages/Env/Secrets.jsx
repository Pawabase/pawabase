import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, CopyText, Field, Loading, Modal, PageHead, Table, useAction, when } from "../../components/ui";
import { del, envPath, patch, post, put, useApi } from "../../lib/api";

export default function Secrets({ env }) {
  const base = envPath(env, "/secrets");
  const secrets = useApi(base);
  const [editing, setEditing] = useState(null);
  const [rotating, setRotating] = useState(null);
  const [run, busy] = useAction();
  return (
    <Layout title="Secrets">
      <PageHead
        title="Secrets"
        description={<>Encrypted at rest. Reference them as <code>secret://NAME</code> in settings, webhook headers and block configs, or read <code>ctx.secrets</code> in functions. Values are never shown again.</>}
        actions={<Button variant="primary" onClick={() => setEditing({ name: "", value: "", description: "", isNew: true })}>New secret</Button>}
      />
      <Card flush>
        <Loading state={secrets} empty="No secrets yet.">
          {(data) => (
            <Table
              rows={data.data}
              columns={[
                { label: "Name", render: (s) => <code>{s.name}</code> },
                { label: "Value", render: (s) => <code className="faint">{s.preview}</code> },
                { label: "Description", key: "description" },
                { label: "Version", render: (s) => <span className="muted">v{s.version}</span> },
                { label: "Rotation", render: (s) => (
                  <span className="row" style={{ gap: 6 }}>
                    <span className="muted small">{s.age_days == null ? "—" : `${s.age_days} day${s.age_days === 1 ? "" : "s"} old`}</span>
                    {s.due && <Badge tone="yellow">due</Badge>}
                    {s.rotate_every_days && !s.due && <span className="faint small">every {s.rotate_every_days}d</span>}
                  </span>
                ) },
                { label: "Updated", render: (s) => `${when(s.updated_at)}${s.updated_by ? ` by ${s.updated_by}` : ""}` },
                { label: "", render: (s) => <div className="row">
                  <Button size="sm" variant={s.due ? "primary" : ""} onClick={() => setRotating(s)}>Rotate</Button>
                  <Button size="sm" onClick={() => setEditing({ ...s, value: "" })}>Replace</Button>
                  <Button size="sm" variant="danger" onClick={async () => { if (confirm(`Delete ${s.name}?`) && await run(() => del(`${base}/${s.name}`), "Deleted")) secrets.reload(); }}>Delete</Button>
                </div> },
              ]}
            />
          )}
        </Loading>
      </Card>
      {rotating && <RotateSheet base={base} secret={rotating} onClose={() => setRotating(null)} onDone={() => secrets.reload()} />}
      {editing && <SecretForm base={base} secret={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); secrets.reload(); }} />}
    </Layout>
  );
}

function SecretForm({ base, secret, onClose, onSaved }) {
  const [data, setData] = useState(secret);
  const [run, busy] = useAction();
  return (
    <Modal title={secret.isNew ? "New secret" : `Replace ${secret.name}`} onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.name || !data.value} onClick={async () => {
      if (await run(() => put(`${base}/${data.name}`, { value: data.value, description: data.description || "" }), "Secret saved")) onSaved();
    }}>Save</Button>}>
      <Field label="Name" hint="UPPER_SNAKE_CASE"><input value={data.name} disabled={!secret.isNew} onChange={(e) => setData({ ...data, name: e.target.value.toUpperCase() })} placeholder="STRIPE_API_KEY" /></Field>
      <Field label="Value"><textarea rows={3} value={data.value} onChange={(e) => setData({ ...data, value: e.target.value })} /></Field>
      <Field label="Description"><input value={data.description || ""} onChange={(e) => setData({ ...data, description: e.target.value })} /></Field>
    </Modal>
  );
}

/** Rotate a secret to a value you give or one Pawabase generates; the old value is kept for rollback. */
function RotateSheet({ base, secret, onClose, onDone }) {
  const [mode, setMode] = useState("generate");
  const [value, setValue] = useState("");
  const [every, setEvery] = useState(secret.rotate_every_days || "");
  const [made, setMade] = useState(null);
  const [run, busy] = useAction();
  const rotate = async () => {
    const answer = await run(() => post(`${base}/${secret.name}/rotate`, mode === "generate" ? {} : { value }), "Rotated");
    if (answer && answer !== true) { setMade(answer); onDone(); }
  };
  const remind = async () => { if (await run(() => patch(`${base}/${secret.name}`, { rotate_every_days: Number(every) || 0 }), every ? "Reminder saved" : "Reminder cleared")) onDone(); };
  const rollback = async () => { if (confirm(`Put back the value ${secret.name} had before its last change?`) && await run(() => post(`${base}/${secret.name}/rollback`), "Rolled back")) { onDone(); onClose(); } };
  return (
    <Modal title={`Rotate ${secret.name}`} onClose={onClose} footer={made ? <Button variant="primary" onClick={onClose}>Done</Button> : <Button variant="primary" disabled={busy || (mode === "value" && !value)} onClick={rotate}>Rotate</Button>}>
      {made ? (
        <div className="stack">
          <div className="alert ok"><b>{secret.name} is now version {made.version}.</b> Everything that reads it picks up the new value within seconds.</div>
          {made.value && <Field label="The new value" hint="Shown once. Copy it into the service that needs to know it, then close this."><CopyText text={made.value} /></Field>}
        </div>
      ) : (
        <>
          <p className="muted" style={{ margin: 0 }}>Rotating replaces the value and keeps the old one, so a mistake can be undone. Update the other side (a provider, a client) as you rotate.</p>
          <Field label="New value">
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="generate">Generate a random value</option>
              <option value="value">I will enter it</option>
            </select>
          </Field>
          {mode === "value" && <Field label="Value"><textarea rows={3} value={value} onChange={(e) => setValue(e.target.value)} /></Field>}
          <section className="form-section">
            <div className="form-section-head"><div><h3>Reminder</h3><p>Show this secret as due for rotation after this many days. Leave empty for none.</p></div></div>
            <div className="row"><input type="number" min={1} value={every} onChange={(e) => setEvery(e.target.value)} placeholder="Days" style={{ maxWidth: 140 }} /><Button disabled={busy} onClick={remind}>Save reminder</Button></div>
          </section>
          {secret.has_previous && (
            <section className="form-section">
              <div className="form-section-head"><div><h3>Go back</h3><p>Restore the value this secret had before its latest change.</p></div><Button size="sm" disabled={busy} onClick={rollback}>Roll back</Button></div>
            </section>
          )}
        </>
      )}
    </Modal>
  );
}
