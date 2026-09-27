import { useState } from "react";
import Layout, { SECTION_TONES } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, CopyText, EmptyState, Loading, Modal, PageHead, Table, formatCell } from "../../components/ui";
import { DefinitionSheet, singular } from "../../components/definitions/DefinitionSheet";
import { KINDS, editable, keyOf } from "../../lib/kinds";
import { del, envPath, get, post, put, useApi } from "../../lib/api";

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

export default function Definitions({ project, env, kind }) {
  const meta = KINDS[kind];
  const base = envPath(project.ref, env, `/${kind}`);
  const list = useApi(base);
  const [editing, setEditing] = useState(null);
  const [reveal, setReveal] = useState(null);
  const [extra, setExtra] = useState(null);
  const create = () => setEditing({ isNew: true, body: meta.blank || meta.template });
  return (
    <Layout title={meta.title}>
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
              columns={meta.columns.map((c) => ({ label: c.replace(/_/g, " "), key: c, render: RENDER[c] }))}
            />
          )}
        </Loading>
      </Card>
      {editing && (
        <DefinitionSheet
          kind={kind}
          project={project}
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
            records: { label: "Records", render: () => <Records base={envPath(project.ref, env, `/resources/${editing.key}/records`)} /> },
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

function Records({ base }) {
  const records = useApi(base, { params: { per_page: 20 } });
  return (
    <Card flush title="Latest records" actions={<Button size="sm" onClick={records.reload}>Refresh</Button>}>
      <Loading state={records} empty="No records (or the table does not exist yet — use Create / migrate table).">
        {(data) => {
          const rows = data.data || [];
          const cols = Object.keys(rows[0] || {}).slice(0, 8);
          return <Table rows={rows} columns={cols.map((c) => ({ label: c, key: c, render: (r) => formatCell(r[c]) }))} />;
        }}
      </Loading>
    </Card>
  );
}
