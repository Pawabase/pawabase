import { useEffect, useState } from "react";
import Layout, { SECTION_TONES, WEBHOOK_PAGES, webhooksSubnav } from "../../components/Layout";
import { useDataSubnav } from "../../components/DataNav";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, CopyText, EmptyState, Loading, Modal, PageHead, Table, formatCell } from "../../components/ui";
import { DefinitionSheet, singular } from "../../components/definitions/DefinitionSheet";
import { KINDS, editable, keyOf } from "../../lib/kinds";
import { del, envPath, get, post, put, useApi } from "../../lib/api";

// Only these Definitions kinds belong to the Data area (they're two of DataNav's own Tools); everything else here
// (policies, routes, subscriptions, schedules, inbound-hooks) gets no secondary sidebar at all.
const DATA_KINDS = ["schemas", "transformers"];

const RENDER = {
  enabled: (r) => <Badge tone={r.enabled ? "green" : ""}><span className="dot w-[7px] h-[7px] rounded-full bg-[var(--faint)] flex-none inline-block [&.ok]:bg-ok [&.warn]:bg-warn [&.bad]:bg-danger [&.info]:bg-info" />{r.enabled ? "On" : "Off"}</Badge>,
  name: (r) => <b>{r.name}</b>,
  slug: (r) => <b className="mono font-mono text-ui-sm">{r.slug}</b>,
  path: (r) => <b className="mono font-mono text-ui-sm">{r.path}</b>,
  method: (r) => <span className={`op-method font-mono text-[10.5px] font-bold p-[2px_7px] rounded-sm m-${r.method}`}>{r.method}</span>,
  events: (r) => <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 4 }}>{(r.events || []).map((e) => <Badge key={e} tone="brand">{e}</Badge>)}</div>,
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
  const { subnav } = useDataSubnav(env, kind);
  const [editing, setEditing] = useState(null);
  const [reveal, setReveal] = useState(null);
  const [extra, setExtra] = useState(null);
  const create = () => setEditing({ isNew: true, body: meta.blank || meta.template });
  // ?new=1 (the top bar's New menu) opens the create sheet straight away, once.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.has("new")) return;
    create();
    params.delete("new");
    window.history.replaceState({}, "", `${window.location.pathname}${params.toString() ? `?${params}` : ""}`);
  }, [kind]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Layout title={meta.title} subnav={WEBHOOK_PAGES.some(([page]) => page === kind) ? webhooksSubnav(env, kind) : DATA_KINDS.includes(kind) ? subnav : undefined}>
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
              ]}
            />
          )}
        </Loading>
      </Card>
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
              </>
            );
          }}
        />
      )}
      {extra && (
        <Modal title={extra.title} onClose={() => setExtra(null)} footer={<Button variant="primary" onClick={() => setExtra(null)}>Done</Button>}>
          {extra.value?.url ? <CopyText text={extra.value.url} /> : <pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg">{JSON.stringify(extra.value, null, 2)}</pre>}
        </Modal>
      )}
      {reveal && (
        <Modal title="Signing secret" onClose={() => setReveal(null)} footer={<Button variant="primary" onClick={() => setReveal(null)}>I have stored it</Button>}>
          <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] warn">Shown once. Store it where the other side can verify signatures.</div>
          <CopyText text={reveal.secret || reveal.key} />
        </Modal>
      )}
    </Layout>
  );
}
