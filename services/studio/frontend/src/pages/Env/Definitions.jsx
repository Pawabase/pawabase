import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, CopyText, Field, JsonInput, Loading, Modal, PageHead, Table, formatCell, useAction } from "../../components/ui";
import { KINDS, editable, keyOf } from "../../lib/kinds";
import { del, envPath, get, post, put, useApi } from "../../lib/api";

export default function Definitions({ project, env, kind }) {
  const meta = KINDS[kind];
  const base = envPath(project.ref, env, `/${kind}`);
  const list = useApi(base);
  const [editing, setEditing] = useState(null);
  const [reveal, setReveal] = useState(null);
  return (
    <Layout title={meta.title}>
      <PageHead
        title={meta.title}
        description={meta.description}
        actions={<Button variant="primary" onClick={() => setEditing({ isNew: true, body: meta.template })}>New</Button>}
      />
      <Card flush>
        <Loading state={list} empty={`No ${meta.title.toLowerCase()} yet.`}>
          {(data) => (
            <Table
              rows={data.data}
              onRowClick={(row) => setEditing({ isNew: false, key: keyOf(kind, row), body: editable(row), row })}
              columns={meta.columns.map((c) => ({
                label: c.replace(/_/g, " "),
                key: c,
                render: c === "enabled" ? (r) => <Badge tone={r.enabled ? "green" : ""}>{r.enabled ? "on" : "off"}</Badge> : c === "name" || c === "slug" || c === "path" ? (r) => <b>{r[c]}</b> : undefined,
              }))}
            />
          )}
        </Loading>
      </Card>
      {editing && (
        <Editor
          kind={kind}
          base={base}
          project={project}
          env={env}
          editing={editing}
          onClose={() => setEditing(null)}
          onSaved={(result) => {
            setEditing(null);
            list.reload();
            if (result && (result.secret || result.key)) setReveal(result);
          }}
        />
      )}
      {reveal && (
        <Modal title="Signing secret" onClose={() => setReveal(null)} footer={<Button variant="primary" onClick={() => setReveal(null)}>Done</Button>}>
          <div className="alert warn">Shown once. Store it where the other side can verify signatures.</div>
          <CopyText text={reveal.secret || reveal.key} />
        </Modal>
      )}
    </Layout>
  );
}

function Editor({ kind, base, project, env, editing, onClose, onSaved }) {
  const [body, setBody] = useState(editing.body);
  const [run, busy] = useAction();
  const [extra, setExtra] = useState(null);
  const save = async () => {
    const result = await run(() => (editing.isNew ? post(base, body) : put(`${base}/${editing.key}`, body)), "Saved");
    if (result) onSaved(result);
  };
  const remove = async () => {
    if (!confirm(`Delete ${editing.key}?`)) return;
    if (await run(() => del(`${base}/${editing.key}`), "Deleted")) onSaved(null);
  };
  const action = async (label, fn) => {
    const result = await run(fn, label);
    if (result) setExtra(result);
  };
  return (
    <Modal
      wide
      title={editing.isNew ? `New ${KINDS[kind].title.replace(/s$/, "").toLowerCase()}` : String(editing.key)}
      onClose={onClose}
      footer={
        <>
          {!editing.isNew && <Button variant="danger" onClick={remove} disabled={busy}>Delete</Button>}
          <span className="grow" />
          {!editing.isNew && kind === "webhooks" && <Button onClick={() => action("Test delivery queued", () => post(`${base}/${editing.key}/test`))}>Send test</Button>}
          {!editing.isNew && kind === "schedules" && <Button onClick={() => action("Schedule fired", () => post(`${base}/${editing.key}/run`))}>Run now</Button>}
          {!editing.isNew && kind === "inbound-hooks" && <Button onClick={() => action(null, () => get(`${base}/${editing.key}/url`))}>Show URL</Button>}
          {!editing.isNew && kind === "resources" && <Button onClick={() => action("Table migrated", () => post(`${base}/${editing.key}/migrate`))}>Create / migrate table</Button>}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={save} disabled={busy || body === null}>Save</Button>
        </>
      }
    >
      <Field label="Definition (JSON)" hint={hintFor(kind)}>
        <JsonInput value={body} onChange={setBody} rows={22} />
      </Field>
      {kind === "resources" && !editing.isNew && <Records base={envPath(project.ref, env, `/resources/${editing.key}/records`)} />}
      {extra && <pre className="code-block">{JSON.stringify(extra, null, 2)}</pre>}
    </Modal>
  );
}

function hintFor(kind) {
  return {
    schemas: "Field types: string, text, integer, number, boolean, datetime, date, uuid, email, url, json, array (items), object (fields), ref (schema).",
    policies: "Operators: all, any, not, eq, ne, gt, gte, lt, lte, in, contains, matches… Operands starting with $ are paths ($auth.user_id, $record.owner_id). Shorthands: authenticated, role, permission, owner, service, scope, aal.",
    resources: "Operation policies accept a policy name, a condition, or null (operators and secret keys only). Create the table after saving.",
    routes: "handler_type is flow or function; handler is its name. Paths may contain {params}.",
    "mail-templates": "{{ name }} values come from the data the sender passes.",
    subscriptions: "Events support wildcards: resource events are <resource>.created|updated|deleted (todos.*), Akountz emits user.* and session.*. condition is a policy condition over $event.",
    schedules: "Set cron (UTC) or interval_seconds.",
    webhooks: "Leave out secret to have one generated; it is shown once.",
    "inbound-hooks": "verification: none, hmac-sha256, pawabase or token.",
    transformers: "Steps run in order: omit, pick, set, rename, case.",
  }[kind];
}

function Records({ base }) {
  const records = useApi(base, { params: { per_page: 20 } });
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="spread"><h3>Records</h3><Button size="sm" onClick={records.reload}>Refresh</Button></div>
      <div className="card flush">
        <Loading state={records} empty="No records (or the table does not exist yet).">
          {(data) => {
            const rows = data.data || [];
            const cols = Object.keys(rows[0] || {}).slice(0, 8);
            return <Table rows={rows} columns={cols.map((c) => ({ label: c, key: c, render: (r) => formatCell(r[c]) }))} />;
          }}
        </Loading>
      </div>
    </div>
  );
}
