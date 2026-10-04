import { useEffect, useRef, useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, JsonInput, Loading, PageHead, Sheet, Table, useAction, when } from "../../components/ui";
import { useApi } from "../../lib/api";
import { useRealtimeSocket } from "../../lib/realtime";

const RT = { service: "realtime" };
const STATUS_LABEL = { open: "Live", connecting: "Connecting…", closed: "Reconnecting…" };

export default function Realtime({ env }) {
  const base = `/${env}`;
  const channels = useApi(`${base}/channels`, { ...RT, interval: 4000 });
  const connections = useApi(`${base}/connections`, { ...RT, interval: 4000 });
  const activity = useApi(`${base}/activity`, { ...RT, interval: 2500 });
  const [watching, setWatching] = useState(null);
  const [target, setTarget] = useState("");
  const socket = useRealtimeSocket(env);

  return (
    <Layout title="Realtime">
      <PageHead
        title="Realtime"
        description={<>A live console on Angula's own socket protocol — broadcast, presence and history over one connection at <code>/realtime/v1/socket</code>. Channel rules live in the environment's settings (realtime.channels).</>}
        actions={<ConnectionBadge status={socket.status} />}
      />

      <div className="stack lg">
        <div className="grid">
          <StatTile icon="users" value={connections.data?.data?.length ?? "–"} label="connections" />
          <StatTile icon="realtime" value={channels.data?.data?.length ?? "–"} label="active channels" />
          <StatTile icon="pulse" value={socket.stats.received} label="messages seen (this console)" />
          <StatTile icon="events" value={activity.data?.errors?.length ?? 0} label="policy errors" tone={activity.data?.errors?.length ? "danger" : undefined} />
        </div>

        <Card flush title="Channels" actions={
          <form
            className="row"
            onSubmit={(e) => { e.preventDefault(); if (target.trim()) { setWatching(target.trim()); setTarget(""); } }}
          >
            <input placeholder="channel:name to watch" value={target} onChange={(e) => setTarget(e.target.value)} style={{ width: 220 }} />
            <Button size="sm" variant="primary" type="submit" disabled={!target.trim()}><Icon name="eye" />Watch</Button>
          </form>
        }>
          <Loading state={channels} empty="No active channels. Watch one by name above, or subscribe from a client.">
            {(data) => (
              <Table
                rows={(data.data || []).map((c) => ({ ...c, id: c.name }))}
                onRowClick={(c) => setWatching(c.name)}
                columns={[
                  { label: "Channel", render: (c) => <code>{c.name}</code> },
                  { label: "Subscribers", key: "subscribers" },
                  { label: "Present", key: "presence" },
                ]}
              />
            )}
          </Loading>
        </Card>

        <div className="stack lg">
          <Card flush title="Connections">
            <Loading state={connections} empty="Nobody is connected.">
              {(data) => (
                <Table
                  rows={data.data || []}
                  columns={[
                    { label: "Connection", render: (c) => <code>{String(c.id).slice(0, 10)}</code> },
                    { label: "User", render: (c) => c.user_id || <Badge>anon</Badge> },
                    { label: "Channels", render: (c) => (c.channels || []).length },
                    { label: "Since", render: (c) => when(c.connected_at) },
                  ]}
                />
              )}
            </Loading>
          </Card>
          <Card flush title="Live activity" actions={<span className="live-dot" title="Refreshing" />}>
            <Loading state={activity} empty="Nothing delivered yet.">
              {(data) => (
                <Table
                  rows={(data.deliveries || []).slice(0, 12).map((d, i) => ({ ...d, id: i }))}
                  onRowClick={(d) => setWatching(d.channel)}
                  columns={[
                    { label: "Channel", render: (d) => <code>{d.channel}</code> },
                    { label: "Event", key: "event" },
                    { label: "Delivered", render: (d) => (d.dropped || d.failed ? <span className="error-text">{d.delivered} ({d.dropped + d.failed} missed)</span> : d.delivered) },
                    { label: "When", render: (d) => when(d.at) },
                  ]}
                  empty="Nothing delivered yet."
                />
              )}
            </Loading>
          </Card>
        </div>
      </div>

      {watching && <ChannelSheet channel={watching} socket={socket} onClose={() => setWatching(null)} />}
    </Layout>
  );
}

function ConnectionBadge({ status }) {
  return (
    <span className={`conn-badge ${status}`}>
      <span className="dot" />
      {STATUS_LABEL[status] || status}
    </span>
  );
}

function StatTile({ icon, value, label, tone }) {
  return (
    <div className={`card stat-tile ${tone || ""}`}>
      <span className="tile-icon"><Icon name={icon} /></span>
      <div className="stack" style={{ gap: 2 }}><b>{value}</b><span>{label}</span></div>
    </div>
  );
}

/** The live console for one channel: a real subscription over the shared
 *  socket (messages arrive as they're published, presence updates live),
 *  with recent history backfilled on open and a publish box to test with. */
function ChannelSheet({ channel, socket, onClose }) {
  const [feed, setFeed] = useState([]);
  const [members, setMembers] = useState(null);
  const [joined, setJoined] = useState(false);
  const [error, setError] = useState(null);
  const bottomRef = useRef(null);
  const seen = useRef(new Set());

  useEffect(() => {
    let cancelled = false;
    setFeed([]);
    setMembers(null);
    setJoined(false);
    setError(null);
    seen.current = new Set();

    const off = socket.on((msg) => {
      if (msg.channel !== channel && msg.type !== "ack") return;
      if (msg.type === "message") {
        const key = msg.id || `${msg.sent_at}:${msg.event}`;
        if (seen.current.has(key)) return;
        seen.current.add(key);
        setFeed((f) => [...f.slice(-199), msg]);
      } else if (msg.type === "presence_state") {
        setMembers(msg.members || []);
      } else if (msg.type === "presence_diff") {
        setMembers((m) => {
          const base = m || [];
          const left = new Set((msg.leaves || []).map((x) => x.connection_id || x.id));
          const kept = base.filter((x) => !left.has(x.connection_id || x.id));
          return [...kept, ...(msg.joins || [])];
        });
      }
    });

    socket.subscribe(channel, { presence: true, since: 0 })
      .then((ack) => { if (!cancelled) { if (ack.ok === false) setError(ack.reason || ack.error); else setJoined(true); } })
      .catch((err) => !cancelled && setError(err.message));

    return () => {
      cancelled = true;
      off();
      socket.unsubscribe(channel).catch(() => {});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel, socket.subscribe, socket.unsubscribe, socket.on]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ block: "nearest" }); }, [feed.length]);

  return (
    <Sheet title={<code>{channel}</code>} subtitle={joined ? "Subscribed — watching live" : error ? "Could not subscribe" : "Subscribing…"} icon="realtime" tone="lavender" onClose={onClose}>
      <div className="stack" style={{ gap: 16 }}>
        {error && <div className="alert error">{error}</div>}

        <div>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
            <h3 style={{ margin: 0 }}>Live messages</h3>
            <span className="faint small">{feed.length} shown</span>
          </div>
          <div className="rt-feed">
            {feed.length === 0 && <p className="faint" style={{ padding: 12 }}>{joined ? "Nothing published yet. Send one below, or from a client." : "Waiting to subscribe…"}</p>}
            {feed.map((m, i) => (
              <div key={m.id || i} className="rt-msg">
                <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
                  <b className="mono">{m.event}</b>
                  <span className="faint small">{m.from ? `from ${m.from}` : "server"} · {when(m.sent_at)}</span>
                </div>
                <pre className="faint" style={{ fontSize: 11.5, margin: "4px 0 0" }}>{typeof m.payload === "string" ? m.payload : JSON.stringify(m.payload)}</pre>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        </div>

        <div>
          <h3 style={{ margin: "0 0 6px" }}>Presence {members != null && <span className="faint small">({members.length})</span>}</h3>
          {members == null ? (
            <p className="faint small">Presence is off for this channel, or not loaded yet.</p>
          ) : members.length === 0 ? (
            <p className="faint small">Nobody is present.</p>
          ) : (
            <div className="row wrap" style={{ gap: 6 }}>
              {members.map((m) => <Badge key={m.connection_id || m.id || m.user_id}>{m.user_id || m.connection_id || "anon"}</Badge>)}
            </div>
          )}
        </div>

        <PublishBox channel={channel} socket={socket} />
      </div>
    </Sheet>
  );
}

function PublishBox({ channel, socket }) {
  const [event, setEvent] = useState("message");
  const [payload, setPayload] = useState({ text: "hello from Studio" });
  const [run, busy] = useAction();
  const [last, setLast] = useState(null);
  return (
    <div>
      <h3 style={{ margin: "0 0 6px" }}>Publish a test message</h3>
      <div className="row" style={{ alignItems: "flex-end", flexWrap: "wrap" }}>
        <Field label="Event"><input value={event} onChange={(e) => setEvent(e.target.value)} /></Field>
      </div>
      <div style={{ marginTop: 10 }}><Field label="Payload"><JsonInput value={payload} onChange={setPayload} rows={3} /></Field></div>
      <div style={{ marginTop: 10 }}>
        <Button
          variant="primary"
          disabled={busy}
          onClick={() => run(async () => { const ack = await socket.publish(channel, event, payload); setLast(ack); }, "Published — watch it land above")}
        >
          Send
        </Button>
      </div>
      {last?.ok === false && <div className="alert error" style={{ marginTop: 10 }}>{last.reason || last.error}</div>}
    </div>
  );
}
