// What happened in your app: every event that was emitted, which flows listen for it, and every flow run.

import { router } from "@inertiajs/react";
import { useState } from "react";
import { Badge, Button, Card, Field, Json, JsonInput, Loading, Modal, Status, Table, useAction, when } from "../../../components/ui";
import { envPath, get, post, useApi } from "../../../lib/api";

export function EventLog({ env }) {
  const base = envPath(env);
  const [name, setName] = useState("");
  const [open, setOpen] = useState(null);
  const events = useApi(`${base}/events`, { params: { name, limit: 100 }, interval: 5000 });
  return (
    <>
      <Card bleed icon="events" flush title={<input placeholder="Filter by event name, e.g. orders.created" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 340 }} aria-label="Event name" />}>
        <Loading state={events} empty="No events yet.">
          {(data) => <Table rows={data.data} onRowClick={async (e) => setOpen(await get(`${base}/events/${e.id}`))} columns={[
            { label: "Event", render: (e) => <code>{e.name}</code> },
            { label: "Source", key: "source" },
            { label: "By", key: "actor" },
            { label: "Data", key: "payload" },
            { label: "When", render: (e) => when(e.created_at) },
          ]} />}
        </Loading>
      </Card>
      {open && <Modal wide title="Event" onClose={() => setOpen(null)}><Json value={open} /></Modal>}
    </>
  );
}

export function EventMap({ env }) {
  const graph = useApi(`${envPath(env)}/events-graph`);
  return (
    <Card bleed flush>
      <Loading state={graph} empty="Nothing produces or listens for events yet.">
        {(data) => <Table rows={data.data.map((r) => ({ ...r, id: r.event }))} columns={[
          { label: "Sent by", render: (r) => <div className="row wrap">{r.producers.map((p) => <Badge key={p} tone="blue">{p}</Badge>)}</div> },
          { label: "Event", render: (r) => <code>{r.event}</code> },
          { label: "Handled by", render: (r) => <div className="row wrap">{r.consumers.map((c) => <Badge key={typeof c === "string" ? c : JSON.stringify(c)} tone="green">{typeof c === "string" ? c : c.name || JSON.stringify(c)}</Badge>)}</div> },
        ]} />}
      </Loading>
    </Card>
  );
}

export function FlowRuns({ env }) {
  const base = envPath(env);
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState(null);
  const runs = useApi(`${base}/flow-runs`, { params: { status, limit: 100 }, interval: 5000 });
  return (
    <>
      <Card bleed icon="flows" flush title={<select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 160 }} aria-label="Status"><option value="">Every status</option><option value="succeeded">Succeeded</option><option value="failed">Failed</option><option value="running">Running</option></select>}>
        <Loading state={runs} empty="No flow runs yet.">
          {(data) => <Table rows={data.data} onRowClick={async (r) => setOpen(await get(`${base}/flow-runs/${r.id}`))} columns={[
            { label: "Flow", render: (r) => <a href="#" onClick={(e) => { e.preventDefault(); e.stopPropagation(); router.visit(`/envs/${env}/flows/${r.flow}`); }} style={{ color: "var(--brand)" }}>{r.flow}</a> },
            { label: "Status", render: (r) => <Status value={r.status} /> },
            { label: "Started by", key: "trigger" },
            { label: "Took", render: (r) => (r.duration_ms != null ? `${Math.round(r.duration_ms)} ms` : "—") },
            { label: "Error", render: (r) => r.error && <span className="error-text text-danger text-ui-sm font-medium">{r.error}</span> },
            { label: "When", render: (r) => when(r.created_at) },
          ]} />}
        </Loading>
      </Card>
      {open && <Modal wide title="Flow run" onClose={() => setOpen(null)}><Json value={open} /></Modal>}
    </>
  );
}

export function EmitEvent({ env, onClose }) {
  const base = envPath(env);
  const [name, setName] = useState("custom.test");
  const [payload, setPayload] = useState({ hello: "world" });
  const [run, busy] = useAction();
  return (
    <Modal title="Send a test event" onClose={onClose} footer={<Button variant="primary" disabled={busy || !name} onClick={async () => { if (await run(() => post(`${base}/events`, { name, payload }), "Event sent")) onClose(); }}>Send</Button>}>
      <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="Data"><JsonInput value={payload} onChange={setPayload} rows={8} /></Field>
    </Modal>
  );
}
