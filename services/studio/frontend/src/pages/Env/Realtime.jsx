import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Field, Json, JsonInput, Loading, PageHead, Table, useAction, when } from "../../components/ui";
import { post, useApi } from "../../lib/api";

const RT = { service: "realtime" };

export default function Realtime({ project, env }) {
  const base = `/${project.ref}/${env}`;
  const channels = useApi(`${base}/channels`, { ...RT, interval: 5000 });
  const connections = useApi(`${base}/connections`, { ...RT, interval: 5000 });
  const activity = useApi(`${base}/activity`, { ...RT, interval: 5000 });
  const [channel, setChannel] = useState(null);
  const detail = useApi(channel ? `${base}/channels/${encodeURIComponent(channel)}` : null, RT);
  return (
    <Layout title="Realtime">
      <PageHead title="Realtime" description={<>Angula channels: broadcast, presence and history over one WebSocket at <code>/realtime/v1/socket</code>. Channel rules live in the environment's settings (realtime.channels).</>} />
      <div className="stack lg">
        <Loading state={activity}>{(data) => <div className="grid">{Object.entries(data).filter(([, v]) => typeof v === "number").map(([k, v]) => <div key={k} className="card stat"><b>{v}</b><span>{k.replace(/_/g, " ")}</span></div>)}</div>}</Loading>
        <div className="grid wide">
          <Card flush title="Channels">
            <Loading state={channels} empty="No active channels.">
              {(data) => <Table rows={(data.data || []).map((c) => ({ ...c, id: c.name }))} onRowClick={(c) => setChannel(c.name)} columns={[{ label: "Channel", render: (c) => <code>{c.name}</code> }, { label: "Subscribers", key: "subscribers" }, { label: "Present", key: "presence" }]} />}
            </Loading>
          </Card>
          <Card flush title="Connections">
            <Loading state={connections} empty="Nobody is connected.">
              {(data) => <Table rows={data.data || []} columns={[{ label: "Connection", render: (c) => <code>{String(c.id).slice(0, 10)}</code> }, { label: "User", render: (c) => c.user_id || <Badge>anon</Badge> }, { label: "Channels", render: (c) => (c.channels || []).length }, { label: "Since", render: (c) => when(c.connected_at) }]} />}
            </Loading>
          </Card>
        </div>
        <Publish project={project} env={env} channel={channel} />
        {channel && detail.data && <Card title={channel}><Json value={detail.data} /></Card>}
      </div>
    </Layout>
  );
}

function Publish({ project, env, channel }) {
  const [target, setTarget] = useState(channel || "");
  const [event, setEvent] = useState("message");
  const [payload, setPayload] = useState({ text: "hello from Studio" });
  const [run, busy] = useAction();
  return (
    <Card title="Broadcast">
      <div className="row" style={{ alignItems: "flex-end" }}>
        <Field label="Channel"><input value={target || channel || ""} onChange={(e) => setTarget(e.target.value)} placeholder="room:lobby" /></Field>
        <Field label="Event"><input value={event} onChange={(e) => setEvent(e.target.value)} /></Field>
      </div>
      <div style={{ marginTop: 10 }}><Field label="Payload"><JsonInput value={payload} onChange={setPayload} rows={4} /></Field></div>
      <div style={{ marginTop: 10 }}>
        <Button variant="primary" disabled={busy || !(target || channel)} onClick={() => run(() => post(`/${project.ref}/${env}/publish`, { channel: target || channel, event, payload }, RT), "Broadcast sent")}>Send</Button>
      </div>
    </Card>
  );
}
