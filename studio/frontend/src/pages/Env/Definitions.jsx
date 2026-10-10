import { useState } from "react";
import Layout, { DATA_PAGES, SECTION_TONES, dataSubnav } from "../../components/Layout";
import { TableView } from "./Database";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, CopyText, EmptyState, Field, Loading, Modal, PageHead, Table, formatCell, useAction } from "../../components/ui";
import { DefinitionSheet, singular } from "../../components/definitions/DefinitionSheet";
import { KINDS, editable, keyOf } from "../../lib/kinds";
import { del, envPath, get, patch, post, put, useApi } from "../../lib/api";

const RENDER = {
  enabled: (r) => <Badge tone={r.enabled ? "green" : ""}><span className="dot" />{r.enabled ? "On" : "Off"}</Badge>,
  name: (r) => <b>{r.name}</b>,
  slug: (r) => <b className="mono">{r.slug}</b>,
  path: (r) => <b className="mono">{r.path}</b>,
  method: (r) => <span className={`op-method m-${r.method}`}>{r.method}</span>,
  events: (r) => <div className="row wrap" style={{ gap: 4 }}>{(r.events || []).map((e) => <Badge key={e} tone="brand">{e}</Badge>)}</div>,
  target_type: (r) => <Badge>{r.target_type}</Badge>,
  handler_type: (r) => <Badge>{r.handler_type}</Badge>,
  verification: (r) => <Badge tone={r.verification === "none" ? "yellow" : "green"}>{r.verification}</Badge>,
  cron: (r) => (r.cron ? <code>{r.cron}</code> : formatCell(null)),
  interval_seconds: (r) => (r.interval_seconds ? `every ${r.interval_seconds}s` : formatCell(null)),
};

export default function Definitions({ env, kind }) {
  const meta = KINDS[kind];
  const base = envPath(env, `/${kind}`);
  const list = useApi(base);
  const [editing, setEditing] = useState(null);
  const [reveal, setReveal] = useState(null);
  const [extra, setExtra] = useState(null);
  const [browse, setBrowse] = useState(null);
  const create = () => setEditing({ isNew: true, body: meta.blank || meta.template });
  return (
    <Layout title={meta.title} subnav={DATA_PAGES.some(([page]) => page === kind) ? dataSubnav(env, kind) : undefined}>
      {browse ? <RecordsView env={env} resource={browse} onBack={() => setBrowse(null)} onEdit={() => { setEditing({ isNew: false, key: keyOf(kind, browse), body: editable(browse) }); }} /> : <>
      <PageHead
        title={meta.title}
        description={meta.description}
        actions={<Button variant="primary" onClick={create}><Icon name="plus" />New {singular(kind).toLowerCase()}</Button>}
      />
      <Card flush>
        <Loading state={list}>
          {(data) => data.data.length === 0 ? (
            <EmptyState
              icon={kind}
              tone={SECTION_TONES[kind]}
              title={`No ${meta.title.toLowerCase()} yet`}
              action={<Button variant="primary" onClick={create}><Icon name="plus" />Create the first one</Button>}
            >
              {meta.description}
            </EmptyState>
          ) : (
            <Table
              rows={data.data}
              onRowClick={(row) => setEditing({ isNew: false, key: keyOf(kind, row), body: editable(row) })}
              columns={[
                ...meta.columns.map((c) => ({ label: c.replace(/_/g, " "), key: c, render: RENDER[c] })),
                ...(kind === "resources" ? [{ label: "", render: (row) => <Button size="sm" onClick={(event) => { event.stopPropagation(); setBrowse(row); }}>Records</Button> }] : []),
              ]}
            />
          )}
        </Loading>
      </Card>
      </>}
      {editing && (
        <DefinitionSheet
          kind={kind}
          env={env}
          editing={editing}
          onSave={(body) => (editing.isNew ? post(base, body) : put(`${base}/${editing.key}`, body))}
          onDelete={() => del(`${base}/${editing.key}`)}
          onClose={(result) => {
            setEditing(null);
            if (result === undefined) return;
            list.reload();
            if (result && (result.secret || result.key)) setReveal(result);
          }}
          actions={editing.isNew ? null : (body, run) => {
            const act = async (label, fn) => { const r = await run(fn, label); if (r) setExtra({ title: label || "Result", value: r }); };
            return (
              <>
                {kind === "webhooks" && <Button onClick={() => act("Test delivery queued", () => post(`${base}/${editing.key}/test`))}><Icon name="play" />Send test</Button>}
                {kind === "schedules" && <Button onClick={() => act("Schedule fired", () => post(`${base}/${editing.key}/run`))}><Icon name="play" />Run now</Button>}
                {kind === "inbound-hooks" && <Button onClick={() => act("Hook URL", () => get(`${base}/${editing.key}/url`))}><Icon name="link" />Show URL</Button>}
                {kind === "resources" && <Button onClick={() => act("Table migrated", () => post(`${base}/${editing.key}/migrate`))}><Icon name="database" />Create / migrate table</Button>}
              </>
            );
          }}
          extraTabs={kind === "resources" && !editing.isNew ? {
            records: { label: "Records", render: () => <Records base={envPath(env, `/resources/${editing.key}/records`)} fields={editing.body.fields || []} /> },
          } : {}}
        />
      )}
      {extra && (
        <Modal title={extra.title} onClose={() => setExtra(null)} footer={<Button variant="primary" onClick={() => setExtra(null)}>Done</Button>}>
          {extra.value?.url ? <CopyText text={extra.value.url} /> : <pre className="code-block">{JSON.stringify(extra.value, null, 2)}</pre>}
        </Modal>
      )}
      {reveal && (
        <Modal title="Signing secret" onClose={() => setReveal(null)} footer={<Button variant="primary" onClick={() => setReveal(null)}>I have stored it</Button>}>
          <div className="alert warn">Shown once. Store it where the other side can verify signatures.</div>
          <CopyText text={reveal.secret || reveal.key} />
        </Modal>
      )}
    </Layout>
  );
}

function Records({ base, fields }) {
  const records = useApi(base, { params: { per_page: 20 } });
  const [editing, setEditing] = useState(null);
  const rows = records.data?.data || [];
  const cols = fields.length ? fields.map((f) => f.name) : Object.keys(rows[0] || {}).slice(0, 8);
  const blank = () => Object.fromEntries(fields.map((f) => [f.name, f.default ?? (f.type === "boolean" ? false : "")]));
  return (
    <>
      <Card
        flush
        title="Records"
        actions={
          <>
            <Button size="sm" variant="primary" onClick={() => setEditing({ isNew: true, body: blank() })}><Icon name="plus" />New record</Button>
            <Button size="sm" onClick={records.reload}>Refresh</Button>
          </>
        }
      >
        <Loading state={records} empty="No records (or the table does not exist yet — use Create / migrate table).">
          {() => (
            <Table
              rows={rows}
              onRowClick={(row) => setEditing({ isNew: false, id: row.id, body: row })}
              columns={cols.map((c) => ({ label: c.replace(/_/g, " "), key: c, render: (r) => formatCell(r[c]) }))}
            />
          )}
        </Loading>
      </Card>
      {editing && (
        <RecordEditor
          fields={fields}
          editing={editing}
          onSave={(body) => (editing.isNew ? post(base, body) : patch(`${base}/${editing.id}`, body))}
          onDelete={() => del(`${base}/${editing.id}`)}
          onClose={(changed) => {
            setEditing(null);
            if (changed) records.reload();
          }}
        />
      )}
    </>
  );
}

/** A field-aware form for one record: text/number/boolean/date inputs when the
 *  resource's field types are known, falling back to raw JSON otherwise. */
function RecordEditor({ fields, editing, onSave, onDelete, onClose }) {
  const [body, setBody] = useState(editing.body);
  const [run, busy] = useAction();
  const set = (name, value) => setBody((b) => ({ ...b, [name]: value }));
  const known = new Set(fields.map((f) => f.name));
  const extra = Object.keys(body).filter((k) => !known.has(k) && k !== "id" && k !== "created_at" && k !== "updated_at");

  const save = async () => {
    const ok = await run(async () => { await onSave(body); }, editing.isNew ? "Record created" : "Record saved");
    if (ok) onClose(true);
  };
  const remove = async () => {
    if (!window.confirm("Delete this record? This can't be undone.")) return;
    const ok = await run(onDelete, "Record deleted");
    if (ok) onClose(true);
  };

  return (
    <Modal
      title={editing.isNew ? "New record" : `Record #${editing.id}`}
      onClose={() => onClose(false)}
      footer={
        <>
          {!editing.isNew && <Button onClick={remove} disabled={busy} style={{ marginRight: "auto", color: "var(--danger)" }}>Delete</Button>}
          <Button onClick={() => onClose(false)} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={save} disabled={busy}>{editing.isNew ? "Create" : "Save"}</Button>
        </>
      }
    >
      <div className="stack" style={{ gap: 14 }}>
        {fields.map((f) => (
          <Field key={f.name} label={f.name} hint={f.type}>
            <RecordInput field={f} value={body[f.name]} onChange={(v) => set(f.name, v)} />
          </Field>
        ))}
        {extra.map((k) => (
          <Field key={k} label={k} hint="raw">
            <RecordInput field={{ type: typeof body[k] === "boolean" ? "boolean" : typeof body[k] === "number" ? "number" : "json" }} value={body[k]} onChange={(v) => set(k, v)} />
          </Field>
        ))}
      </div>
    </Modal>
  );
}

function RecordInput({ field, value, onChange }) {
  const type = field.type || "string";
  if (type === "boolean") {
    return (
      <select value={value ? "true" : "false"} onChange={(e) => onChange(e.target.value === "true")}>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  if (type === "integer" || type === "number" || type === "float") {
    return <input type="number" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
  }
  if (type === "datetime" || type === "date") {
    return <input type={type === "date" ? "date" : "datetime-local"} value={value ? String(value).slice(0, type === "date" ? 10 : 16) : ""} onChange={(e) => onChange(e.target.value)} />;
  }
  if (type === "text") {
    return <textarea rows={4} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
  }
  if (type === "object" || type === "array" || type === "json") {
    return (
      <textarea
        rows={4}
        className="mono"
        defaultValue={value === undefined || value === null ? "" : JSON.stringify(value, null, 2)}
        onBlur={(e) => {
          try {
            onChange(e.target.value.trim() === "" ? null : JSON.parse(e.target.value));
          } catch {
            /* leave the previous value; the user is still typing */
          }
        }}
      />
    );
  }
  return <input type="text" value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
}

/** One resource's rows, read from its table, with the way back to the list and to its definition. */
function RecordsView({ env, resource, onBack, onEdit }) {
  const table = resource.table || resource.name;
  const [run, busy] = useAction();
  const [version, setVersion] = useState(0);
  return (
    <div className="stack lg">
      <div className="row wrap">
        <Button size="sm" onClick={onBack}><Icon name="chevronRight" size={14} className="rotate-180" /> Resources</Button>
        <h1 style={{ fontSize: 20 }}>{resource.name}</h1>
        <span className="muted small">table <code>{table}</code></span>
        <span className="grow" />
        <Button size="sm" disabled={busy} onClick={async () => { if (await run(() => post(envPath(env, `/resources/${resource.name}/migrate`)), "Table created or extended")) setVersion((v) => v + 1); }}><Icon name="database" size={14} /> Create / migrate table</Button>
        <Button size="sm" variant="primary" onClick={onEdit}>Edit definition</Button>
      </div>
      <TableView key={`${table}:${version}`} base={envPath(env, "/database")} table={table} />
    </div>
  );
}
