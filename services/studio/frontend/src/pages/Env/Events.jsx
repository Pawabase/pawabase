import { router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Field, Json, JsonInput, Loading, Modal, PageHead, Status, Table, Tabs, useAction, when } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";

export default function Events({ project, env }) {
  const base = envPath(project.ref, env);
  const [tab, setTab] = useState("events");
  const [detail, setDetail] = useState(null);
  const [emitting, setEmitting] = useState(false);
  return (
    <Layout title="Events & runs">
      <PageHead title="Events & runs" description="Everything that happened: events on the bus, the flows they started, webhooks delivered and mail sent."
        actions={<Button onClick={() => setEmitting(true)}>Emit test event</Button>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "events", label: "Events" }, { value: "graph", label: "Event map" }, { value: "runs", label: "Flow runs" }, { value: "deliveries", label: "Webhook deliveries" }, { value: "mail", label: "Mail" }]} />
      {tab === "events" && <EventList base={base} onOpen={async (e) => setDetail(await get(`${base}/events/${e.id}`))} />}
      {tab === "graph" && <Graph base={base} />}
      {tab === "runs" && <Runs base={base} project={project} env={env} onOpen={async (r) => setDetail(await get(`${base}/flow-runs/${r.id}`))} />}
      {tab === "deliveries" && <Deliveries base={base} onOpen={setDetail} />}
      {tab === "mail" && <Mail base={base} />}
      {detail && <Modal wide title="Details" onClose={() => setDetail(null)}><Json value={detail} /></Modal>}
      {emitting && <Emit base={base} onClose={() => setEmitting(false)} />}
    </Layout>
  );
}

function EventList({ base, onOpen }) {
  const [name, setName] = useState("");
  const events = useApi(`${base}/events`, { params: { name, limit: 100 }, interval: 5000 });
  return (
    <Card flush title={<input placeholder="Filter by exact name, e.g. resource.todos.created" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 360 }} />}>
      <Loading state={events} empty="No events yet.">
        {(data) => <Table rows={data.data} onRowClick={onOpen} columns={[{ label: "Event", render: (e) => <code>{e.name}</code> }, { label: "Source", key: "source" }, { label: "Actor", key: "actor" }, { label: "Payload", key: "payload" }, { label: "When", render: (e) => when(e.created_at) }]} />}
      </Loading>
    </Card>
  );
}

function Graph({ base }) {
  const graph = useApi(`${base}/events-graph`);
  return (
    <Card flush>
      <Loading state={graph} empty="No producers or consumers defined yet.">
        {(data) => <Table rows={data.data.map((r) => ({ ...r, id: r.event }))} columns={[
          { label: "Producers", render: (r) => <div className="row wrap">{r.producers.map((p) => <Badge key={p} tone="blue">{p}</Badge>)}</div> },
          { label: "Event", render: (r) => <code>{r.event}</code> },
          { label: "Consumers", render: (r) => <div className="row wrap">{r.consumers.map((c) => <Badge key={typeof c === "string" ? c : JSON.stringify(c)} tone="green">{typeof c === "string" ? c : c.name || JSON.stringify(c)}</Badge>)}</div> },
        ]} />}
      </Loading>
    </Card>
  );
}

function Runs({ base, project, env, onOpen }) {
  const [status, setStatus] = useState("");
  const runs = useApi(`${base}/flow-runs`, { params: { status, limit: 100 }, interval: 5000 });
  return (
    <Card flush title={<select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 160 }}><option value="">every status</option><option>succeeded</option><option>failed</option><option>running</option></select>}>
      <Loading state={runs} empty="No runs yet.">
        {(data) => <Table rows={data.data} onRowClick={onOpen} columns={[
          { label: "Flow", render: (r) => <a href="#" onClick={(e) => { e.preventDefault(); e.stopPropagation(); router.visit(`/projects/${project.ref}/${env}/flows/${r.flow}`); }} style={{ color: "var(--accent)" }}>{r.flow}</a> },
          { label: "Status", render: (r) => <Status value={r.status} /> }, { label: "Trigger", key: "trigger" },
          { label: "Duration", render: (r) => (r.duration_ms != null ? `${Math.round(r.duration_ms)} ms` : "—") },
          { label: "Error", render: (r) => r.error && <span className="error-text">{r.error}</span> }, { label: "When", render: (r) => when(r.created_at) },
        ]} />}
      </Loading>
    </Card>
  );
}

function Deliveries({ base, onOpen }) {
  const deliveries = useApi(`${base}/webhook-deliveries`, { interval: 5000 });
  const [run] = useAction();
  return (
    <Card flush>
      <Loading state={deliveries} empty="No deliveries yet.">
        {(data) => <Table rows={data.data} onRowClick={onOpen} columns={[
          { label: "Endpoint", key: "endpoint" }, { label: "Event", render: (d) => <code>{d.event}</code> }, { label: "Status", render: (d) => <Status value={d.status} /> },
          { label: "HTTP", key: "response_status" }, { label: "Attempts", key: "attempts" }, { label: "When", render: (d) => when(d.created_at) },
          { label: "", render: (d) => <Button size="sm" onClick={async (e) => { e.stopPropagation(); if (await run(() => post(`${base}/webhook-deliveries/${d.id}/redeliver`), "Redelivery queued")) deliveries.reload(); }}>Redeliver</Button> },
        ]} />}
      </Loading>
    </Card>
  );
}

function Mail({ base }) {
  const log = useApi(`${base}/mail/log`, { interval: 10000 });
  const [to, setTo] = useState("");
  const [run, busy] = useAction();
  return (
    <div className="stack lg">
      <Card title="Send a test message">
        <div className="row"><input placeholder="you@example.com" value={to} onChange={(e) => setTo(e.target.value)} /><Button disabled={busy || !to} onClick={() => run(() => post(`${base}/mail/test`, { to: [to] }), "Test message queued")}>Send</Button></div>
      </Card>
      <Card flush title="Mail log">
        <Loading state={log} empty="Nothing sent yet.">
          {(data) => <Table rows={data.data} columns={[{ label: "To", key: "to" }, { label: "Subject", key: "subject" }, { label: "Template", key: "template" }, { label: "Status", render: (m) => <Status value={m.status} /> }, { label: "Error", key: "error" }, { label: "When", render: (m) => when(m.created_at) }]} />}
        </Loading>
      </Card>
    </div>
  );
}

function Emit({ base, onClose }) {
  const [name, setName] = useState("custom.test");
  const [payload, setPayload] = useState({ hello: "world" });
  const [run, busy] = useAction();
  return (
    <Modal title="Emit an event" onClose={onClose} footer={<Button variant="primary" disabled={busy || !name} onClick={async () => { if (await run(() => post(`${base}/events`, { name, payload }), "Event emitted")) onClose(); }}>Emit</Button>}>
      <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="Payload"><JsonInput value={payload} onChange={setPayload} rows={8} /></Field>
    </Modal>
  );
}
