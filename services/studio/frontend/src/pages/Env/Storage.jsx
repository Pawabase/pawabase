import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, CopyText, Field, JsonInput, Loading, Modal, PageHead, Table, useAction } from "../../components/ui";
import { KINDS, editable } from "../../lib/kinds";
import { del, envPath, post, put, useApi } from "../../lib/api";

export default function Storage({ project, env }) {
  const base = envPath(project.ref, env, "/buckets");
  const buckets = useApi(base);
  const [bucket, setBucket] = useState(null);
  const [editing, setEditing] = useState(null);
  return (
    <Layout title="Storage">
      <PageHead
        title="Storage"
        description="Buckets on the environment's storage driver (local disk or any S3-compatible service), with policies and signed URLs."
        actions={<Button variant="primary" onClick={() => setEditing({ isNew: true, body: KINDS.buckets.template })}>New bucket</Button>}
      />
      <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 16, alignItems: "start" }}>
        <Card flush title="Buckets">
          <Loading state={buckets} empty="No buckets yet.">
            {(data) => (
              <div className="nav" style={{ padding: 6 }}>
                {data.data.map((b) => (
                  <a key={b.name} href="#" className={bucket?.name === b.name ? "active" : ""} onClick={(e) => { e.preventDefault(); setBucket(b); }}>
                    <span className="grow">{b.name}</span>{b.public && <Badge tone="blue">public</Badge>}
                  </a>
                ))}
              </div>
            )}
          </Loading>
        </Card>
        {bucket ? <Objects base={base} bucket={bucket} onEdit={() => setEditing({ isNew: false, body: editable(bucket) })} /> : <Card><div className="empty">Choose a bucket.</div></Card>}
      </div>
      {editing && (
        <BucketEditor base={base} editing={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); buckets.reload(); setBucket(null); }} />
      )}
    </Layout>
  );
}

function Objects({ base, bucket, onEdit }) {
  const [prefix, setPrefix] = useState("");
  const objects = useApi(`${base}/${bucket.name}/objects`, { params: { prefix } });
  const [signed, setSigned] = useState(null);
  const [run, busy] = useAction();
  const upload = async (file) => {
    const key = prefix + file.name;
    const grant = await run(() => post(`${base}/${bucket.name}/sign`, { key, method: "PUT", expires_in: 300 }));
    if (!grant) return;
    await run(async () => {
      const response = await fetch(grant.url, { method: "PUT", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } });
      if (!response.ok) throw new Error(`Upload failed (${response.status}): ${await response.text()}`);
    }, `Uploaded ${file.name}`);
    objects.reload();
  };
  return (
    <Card flush title={<span>{bucket.name} <span className="faint" style={{ fontWeight: 400 }}>/{prefix}</span></span>} actions={<>
      {prefix && <Button size="sm" onClick={() => setPrefix(prefix.split("/").slice(0, -2).join("/") + (prefix.split("/").length > 2 ? "/" : ""))}>Up</Button>}
      <label className="btn sm primary" style={{ opacity: busy ? 0.6 : 1 }}>Upload<input type="file" hidden onChange={(e) => e.target.files[0] && upload(e.target.files[0])} /></label>
      <Button size="sm" onClick={onEdit}>Settings</Button>
    </>}>
      <Loading state={objects}>
        {(data) => (
          <Table
            rows={[...data.prefixes.map((p) => ({ key: p, folder: true })), ...data.files]}
            empty="No objects here."
            columns={[
              { label: "Key", render: (o) => o.folder ? <a href="#" onClick={(e) => { e.preventDefault(); setPrefix(o.key); }} style={{ color: "var(--accent)" }}>📁 {o.key.slice(prefix.length)}</a> : o.key.slice(prefix.length) },
              { label: "Type", key: "content_type" },
              { label: "Size", render: (o) => (o.folder ? "" : bytes(o.size)) },
              { label: "", render: (o) => !o.folder && <div className="row">
                <Button size="sm" onClick={async () => { const r = await run(() => post(`${base}/${bucket.name}/sign`, { key: o.key, method: "GET", expires_in: 3600 })); if (r) setSigned(r.url); }}>Link</Button>
                <Button size="sm" variant="danger" onClick={async () => { if (confirm(`Delete ${o.key}?`) && await run(() => del(`${base}/${bucket.name}/objects/${o.key}`), "Deleted")) objects.reload(); }}>Delete</Button>
              </div> },
            ]}
          />
        )}
      </Loading>
      {signed && <Modal title="Signed link (1 hour)" onClose={() => setSigned(null)}><CopyText text={signed} /></Modal>}
    </Card>
  );
}

function BucketEditor({ base, editing, onClose, onSaved }) {
  const [body, setBody] = useState(editing.body);
  const [run, busy] = useAction();
  return (
    <Modal wide title={editing.isNew ? "New bucket" : `Bucket ${editing.body.name}`} onClose={onClose} footer={<>
      {!editing.isNew && <Button variant="danger" onClick={async () => { if (confirm("Delete the bucket definition? Objects stay in storage.") && await run(() => del(`${base}/${editing.body.name}`), "Deleted")) onSaved(); }}>Delete</Button>}
      <span className="grow" />
      <Button variant="primary" disabled={busy || !body} onClick={async () => { if (await run(() => (editing.isNew ? post(base, body) : put(`${base}/${editing.body.name}`, body)), "Saved")) onSaved(); }}>Save</Button>
    </>}>
      <Field label="Definition" hint="public: anyone may read. read_policy / write_policy: a policy name or condition. accepts: MIME patterns. max_bytes: 0 for no limit."><JsonInput value={body} onChange={setBody} rows={14} /></Field>
    </Modal>
  );
}

function bytes(n) {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
