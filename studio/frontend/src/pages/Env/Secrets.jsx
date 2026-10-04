import { useState } from "react";
import Layout from "../../components/Layout";
import { Button, Card, Field, Loading, Modal, PageHead, Table, useAction, when } from "../../components/ui";
import { del, envPath, put, useApi } from "../../lib/api";

export default function Secrets({ env }) {
  const base = envPath(env, "/secrets");
  const secrets = useApi(base);
  const [editing, setEditing] = useState(null);
  const [run] = useAction();
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
                { label: "Updated", render: (s) => `${when(s.updated_at)}${s.updated_by ? ` by ${s.updated_by}` : ""}` },
                { label: "", render: (s) => <div className="row">
                  <Button size="sm" onClick={() => setEditing({ ...s, value: "" })}>Replace</Button>
                  <Button size="sm" variant="danger" onClick={async () => { if (confirm(`Delete ${s.name}?`) && await run(() => del(`${base}/${s.name}`), "Deleted")) secrets.reload(); }}>Delete</Button>
                </div> },
              ]}
            />
          )}
        </Loading>
      </Card>
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
