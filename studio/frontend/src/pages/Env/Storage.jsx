import { useEffect, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, CopyText, EmptyState, Field, Loading, Modal, PageHead, Table, useAction } from "../../components/ui";
import { Icon } from "../../components/icons";
import { DefinitionSheet } from "../../components/definitions/DefinitionSheet";
import { KINDS, editable } from "../../lib/kinds";
import { del, envPath, post, put, useApi } from "../../lib/api";

export default function Storage({ env }) {
  const base = envPath(env, "/buckets");
  const buckets = useApi(base);
  const [bucket, setBucket] = useState(null);
  const [editing, setEditing] = useState(null);
  const list = buckets.data?.data || [];
  useEffect(() => { if (!bucket && list.length) setBucket(list[0]); }, [list.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const subnav = {
    title: "Storage",
    loading: buckets.loading,
    empty: "No buckets yet. Create one with the + button.",
    action: { label: "New bucket", onClick: () => setEditing({ isNew: true, body: KINDS.buckets.blank }) },
    groups: [{ title: "Buckets", items: list.map((b) => ({ key: b.name, label: b.name, tag: b.public ? "public" : undefined })) }],
    active: bucket?.name,
    onSelect: (name) => setBucket(list.find((b) => b.name === name) || null),
  };
  return (
    <Layout title="Storage" subnav={subnav}>
      <PageHead
        title={bucket ? bucket.name : "Storage"}
        description="Buckets on the environment's storage driver (local disk or any S3-compatible service), with policies and signed URLs."
      />
      {bucket ? <Objects key={bucket.name} base={base} bucket={bucket} onEdit={() => setEditing({ isNew: false, body: editable(bucket) })} /> : (
        <Card><EmptyState icon="storage" tone="sky" title="No bucket yet">Create a bucket with the + button in the column on the left.</EmptyState></Card>
      )}
      {editing && (
        <DefinitionSheet
          kind="buckets"
          env={env}
          editing={{ ...editing, key: editing.body.name }}
          onSave={(body) => (editing.isNew ? post(base, body) : put(`${base}/${editing.body.name}`, body))}
          onDelete={() => del(`${base}/${editing.body.name}`)}
          onClose={(result) => { setEditing(null); if (result !== undefined) { buckets.reload(); setBucket(null); } }}
        />
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
              { label: "Key", render: (o) => o.folder ? <a href="#" onClick={(e) => { e.preventDefault(); setPrefix(o.key); }} style={{ color: "var(--brand)" }}>📁 {o.key.slice(prefix.length)}</a> : o.key.slice(prefix.length) },
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

function bytes(n) {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
