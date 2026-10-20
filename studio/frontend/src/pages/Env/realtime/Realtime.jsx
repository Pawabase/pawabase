import { Link } from "@inertiajs/react";
import { useEffect, useRef, useState } from "react";
import { envHref } from "../../../components/Layout";
import { Icon } from "../../../components/icons";
import { Badge, Button, Card, CopyText, EmptyState, Field, JsonInput, Loading, Modal, PageHead, Sheet, Switch, Table, when, useAction } from "../../../components/ui";
import { editable } from "../../../lib/kinds";
import { del, envPath, get, patch, post, put, useApi } from "../../../lib/api";
import { useRealtimeSocket } from "../../../lib/realtime";
import { Stat, StatStrip } from "../observability/kit";

export const RT = { service: "realtime" };
const STATUS_LABEL = { open: "Live", connecting: "Connecting…", closed: "Reconnecting…" };
const KINDS = [["public", "Public"], ["private", "Private"], ["presence", "Presence"], ["protected", "Protected"], ["ephemeral", "Ephemeral"], ["durable", "Durable"]];
const KIND_HINT = {
  public: "Anyone who can subscribe sees it; no presence tracking unless turned on.",
  private: "Scoped to the caller — a pattern like user:{{ auth.id }} only that user can open.",
  presence: "Tracks who is on the channel, in addition to messages.",
  protected: "Subscribe is open, publish is restricted — a broadcast-only channel.",
  ephemeral: "Nothing is kept for replay; a disconnected subscriber misses what happened while away.",
  durable: "Every publish is written to the database (RealtimeMessage), deduplicated by client_msg_id, and survives a restart.",
};

export function ConnectionBadge({ status }) {
  return <span className={`conn-badge inline-flex items-center gap-[7px] p-[6px_12px] rounded-sm bg-sunken text-ui-sm font-semibold text-muted [&_.dot]:w-2 [&_.dot]:h-2 [&_.dot]:rounded-full [&_.dot]:bg-[var(--faint)] [&.open]:text-ok [&.open]:bg-ok-soft [&.open_.dot]:bg-ok [&.open_.dot]:shadow-none [&.connecting_.dot]:bg-warn [&.connecting_.dot]:animate-[blink_1s_ease-in-out_infinite] [&.closed_.dot]:bg-warn [&.closed_.dot]:animate-[blink_1s_ease-in-out_infinite] [&.connecting]:text-warn [&.connecting]:bg-warn-soft [&.closed]:text-warn [&.closed]:bg-warn-soft ${status}`}><span className="dot w-[7px] h-[7px] rounded-full bg-[var(--faint)] flex-none inline-block [&.ok]:bg-ok [&.warn]:bg-warn [&.bad]:bg-danger [&.info]:bg-info" />{STATUS_LABEL[status] || status}</span>;
}

// ── overview ────────────────────────────────────────────────────────────

export function Overview({ env, socket }) {
  const base = `/${env}`;
  const summary = useApi(`${base}/summary`, { ...RT, interval: 5000 });
  const activity = useApi(`${base}/activity`, { ...RT, interval: 3000 });
  return (
    <>
      <PageHead title="Realtime" description="Channels, connections and presence over WebSocket and Server-Sent Events, with durable channels, database-change subscriptions and webhook fan-out." actions={<ConnectionBadge status={socket.status} />} />
      <Loading state={summary}>
        {(s) => (
          <StatStrip>
            <Stat value={s.connections} label="Connections" />
            <Stat value={s.channels} label="Active channels" />
            <Stat value={s.counters?.published ?? 0} label="Published" />
            <Stat value={s.counters?.delivered ?? 0} label="Delivered" />
            <Stat tone={s.counters?.dropped ? "warn" : undefined} value={s.counters?.dropped ?? 0} label="Dropped" />
          </StatStrip>
        )}
      </Loading>
      <Card icon="pulse" flush title="Live activity" actions={<span className="live-dot w-2 h-2 rounded-full bg-ok shadow-none [&.busy]:bg-brand [&.busy]:animate-[blink_0.9s_ease-in-out_infinite] [&.off]:bg-[var(--faint)] [&.off]:shadow-none" title="Refreshing" />}>
        <Loading state={activity} empty="Nothing delivered yet.">
          {(data) => (
            <Table
              rows={(data.deliveries || []).slice(0, 12).map((d, i) => ({ ...d, id: i }))}
              columns={[
                { label: "Channel", render: (d) => <code>{d.channel}</code> },
                { label: "Event", key: "event" },
                { label: "Delivered", render: (d) => (d.dropped || d.failed ? <span className="error-text text-danger text-ui-sm font-medium">{d.delivered} ({d.dropped + d.failed} missed)</span> : d.delivered) },
                { label: "When", render: (d) => when(d.at) },
              ]}
              empty="Nothing delivered yet."
            />
          )}
        </Loading>
      </Card>
    </>
  );
}

// ── channels (DB-backed rules + what's live right now) ──────────────────

const blankRule = () => ({ pattern: "", kind: "public", subscribe: "authenticated", publish: "authenticated", presence: true, history: 50, message_size_limit: 0, publish_rate_limit: 0, subscription_limit: 0, retention_seconds: 0, description: "" });

export function Channels({ env, onWatch }) {
  const base = envPath(env, "/realtime-channels");
  const rules = useApi(base);
  const live = useApi(`/${env}/channels`, { ...RT, interval: 5000 });
  const [editing, setEditing] = useState(null);
  const [run] = useAction();
  const reload = () => rules.reload();
  return (
    <>
      <PageHead title="Channels" description="Durable rules Studio and the API both read — the DB-backed sibling of the hand-edited channel list in Settings. Patterns support * and {{ auth.* }} templates." actions={<Button variant="primary" onClick={() => setEditing(blankRule())}><Icon name="plus" />New channel rule</Button>} />
      <Card icon="realtime" flush title="Channel rules">
        <Loading state={rules} empty={
          <EmptyState icon="layers" title="No channel rules yet" action={<Button variant="primary" onClick={() => setEditing(blankRule())}><Icon name="plus" />New channel rule</Button>}>
            Every channel falls back to the environment's default policy (Settings → Realtime) until you add a rule here.
          </EmptyState>
        }>
          {(data) => (
            <Table
              rows={data.data}
              onRowClick={(r) => setEditing(r)}
              columns={[
                { label: "Pattern", render: (r) => <code>{r.pattern}</code> },
                { label: "Kind", render: (r) => <Badge tone={r.kind === "durable" ? "lavender" : r.kind === "ephemeral" ? "yellow" : undefined}>{r.kind}</Badge> },
                { label: "Subscribe", key: "subscribe" },
                { label: "Publish", key: "publish" },
                { label: "Presence", render: (r) => (r.presence ? "On" : "Off") },
                { label: "History", key: "history" },
                { label: "", render: (r) => <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" onClick={(e) => e.stopPropagation()}><Button size="sm" onClick={() => onWatch(r.pattern)}><Icon name="eye" size={13} />Watch</Button></div> },
              ]}
            />
          )}
        </Loading>
      </Card>
      <Card icon="pulse" flush title="Live now" actions={<span className="live-dot w-2 h-2 rounded-full bg-ok shadow-none [&.busy]:bg-brand [&.busy]:animate-[blink_0.9s_ease-in-out_infinite] [&.off]:bg-[var(--faint)] [&.off]:shadow-none" title="Refreshing" />}>
        <Loading state={live} empty="No active channels. Watch one by name above, or subscribe from a client.">
          {(data) => (
            <Table
              rows={(data.data || []).map((c) => ({ ...c, id: c.name }))}
              onRowClick={(c) => onWatch(c.name)}
              columns={[
                { label: "Channel", render: (c) => <code>{c.name}</code> },
                { label: "Subscribers", key: "subscribers" },
                { label: "Present", key: "presence" },
              ]}
            />
          )}
        </Loading>
      </Card>
      {editing && (
        <ChannelRuleSheet
          base={base}
          initial={editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }}
          onDeleted={() => { setEditing(null); reload(); }}
        />
      )}
    </>
  );
}

function ChannelRuleSheet({ base, initial, onClose, onSaved, onDeleted }) {
  const [rule, setRule] = useState(initial);
  const [run, busy] = useAction();
  const set = (change) => setRule((current) => ({ ...current, ...change }));
  const isNew = !rule.id;
  const save = async () => {
    const body = { pattern: rule.pattern, kind: rule.kind, subscribe: rule.subscribe, publish: rule.publish, presence: rule.presence, history: rule.history, message_size_limit: rule.message_size_limit, publish_rate_limit: rule.publish_rate_limit, subscription_limit: rule.subscription_limit, retention_seconds: rule.retention_seconds, description: rule.description };
    const done = isNew
      ? await run(() => post(base, body), "Channel rule added")
      : await run(() => patch(`${base}/${rule.id}`, body), "Channel rule saved");
    if (done) onSaved();
  };
  const remove = async () => { if (confirm(`Delete the rule for "${rule.pattern}"?`) && await run(() => del(`${base}/${rule.id}`), "Rule deleted")) onDeleted(); };
  return (
    <Sheet title={isNew ? "New channel rule" : <code>{rule.pattern}</code>} subtitle="Governs who can subscribe or publish, and whether messages are kept." icon="realtime" tone="lavender" onClose={onClose}
      footer={<>{!isNew && <Button variant="danger" disabled={busy} onClick={remove}>Delete</Button>}<span className="grow flex-1 min-w-0" /><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !rule.pattern} onClick={save}>{isNew ? "Add rule" : "Save"}</Button></>}>
      <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Pattern" hint="org:{{ auth.org }} or chat:*"><input autoFocus className="mono font-mono text-ui-sm" value={rule.pattern} onChange={(e) => set({ pattern: e.target.value })} placeholder="orders" /></Field>
          <Field label="Kind" hint={KIND_HINT[rule.kind]}><select value={rule.kind} onChange={(e) => set({ kind: e.target.value })}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        </div>
      </section>
      <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Subscribe policy"><input value={rule.subscribe} onChange={(e) => set({ subscribe: e.target.value })} placeholder="authenticated" /></Field>
          <Field label="Publish policy"><input value={rule.publish} onChange={(e) => set({ publish: e.target.value })} placeholder="authenticated" /></Field>
        </div>
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 18, marginTop: 10 }}>
          <Switch checked={rule.presence} onChange={(v) => set({ presence: v })} label="Presence" hint="Track who is on the channel." />
        </div>
      </section>
      <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
        <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Limits</h3><p>0 keeps the installation default.</p></div></div>
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="History depth"><input type="number" min={0} value={rule.history} onChange={(e) => set({ history: Number(e.target.value) || 0 })} /></Field>
          <Field label="Message size limit (bytes)"><input type="number" min={0} value={rule.message_size_limit} onChange={(e) => set({ message_size_limit: Number(e.target.value) || 0 })} /></Field>
          <Field label="Publish rate limit (per second)"><input type="number" min={0} value={rule.publish_rate_limit} onChange={(e) => set({ publish_rate_limit: Number(e.target.value) || 0 })} /></Field>
          <Field label="Subscriber limit"><input type="number" min={0} value={rule.subscription_limit} onChange={(e) => set({ subscription_limit: Number(e.target.value) || 0 })} /></Field>
          {rule.kind === "durable" && <Field label="Retention (seconds)" hint="How long a durable message is kept before the sweep purges it."><input type="number" min={0} value={rule.retention_seconds} onChange={(e) => set({ retention_seconds: Number(e.target.value) || 0 })} /></Field>}
        </div>
      </section>
      <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
        <Field label="Description" optional><input value={rule.description} onChange={(e) => set({ description: e.target.value })} placeholder="What this channel is for" /></Field>
      </section>
    </Sheet>
  );
}

// ── connections ───────────────────────────────────────────────────────────

export function Connections({ env }) {
  const base = `/${env}`;
  const connections = useApi(`${base}/connections`, { ...RT, interval: 4000 });
  const [run, busy] = useAction();
  return (
    <>
      <PageHead title="Connections" description="Every socket and SSE stream open to this environment right now." />
      <Card flush>
        <Loading state={connections} empty="Nobody is connected.">
          {(data) => (
            <Table
              rows={data.data || []}
              columns={[
                { label: "Connection", render: (c) => <code>{String(c.id).slice(0, 10)}</code> },
                { label: "User", render: (c) => c.user_id || <Badge>anon</Badge> },
                { label: "Channels", render: (c) => (c.channels || []).length },
                { label: "Since", render: (c) => when(c.connected_at) },
                { label: "", render: (c) => (
                  <Button size="sm" variant="danger" disabled={busy} onClick={async () => { if (await run(() => del(`${base}/connections/${c.id}`, RT), "Disconnected")) connections.reload(); }}>Disconnect</Button>
                ) },
              ]}
            />
          )}
        </Loading>
      </Card>
    </>
  );
}

// ── protocols ───────────────────────────────────────────────────────────

const LIVE_PROTOCOLS = [
  {
    key: "websocket", label: "WebSocket", icon: "bolt", tone: "lavender",
    about: "Full duplex: subscribe, publish, presence and history all go over one socket.",
    snippet: (host) => `wss://${host}/realtime/v1/socket?token=…`,
    traits: ["Duplex", "Presence", "History replay"],
  },
  {
    key: "sse", label: "Server-Sent Events", icon: "send", tone: "mint",
    about: "One-way: subscribe to one or more channels and receive messages and presence diffs as events on the stream. Publishing still goes through POST /realtime/v1/publish.",
    snippet: (host) => `${host}/realtime/v1/sse?channels=public:lobby&token=…`,
    traits: ["One-way", "Presence", "Plain HTTP"],
  },
];

const UNAVAILABLE = [
  { key: "mqtt", label: "MQTT", icon: "terminal", tone: "sky", about: "A pub/sub broker protocol used by IoT and embedded clients." },
  { key: "mqtt-ws", label: "MQTT over WebSocket", icon: "link", tone: "butter", about: "MQTT framed over a WebSocket, for browser MQTT clients." },
  { key: "http-streaming", label: "HTTP streaming", icon: "layers", tone: "peach", about: "Chunked-transfer streaming without SSE's event framing." },
  { key: "long-polling", label: "Long polling", icon: "clock", tone: "rose", about: "A fallback transport for clients that cannot hold a streaming connection open." },
];

function ProtocolIcon({ icon, tone }) {
  return <span className={`tile-icon pastel-contrast [&.lavender]:text-lavender [&.peach]:text-peach [&.mint]:text-mint [&.butter]:text-butter [&.sky]:text-sky [&.rose]:text-rose ${tone || ""}`}><Icon name={icon} size={24} /></span>;
}

export function Protocols({ env }) {
  const host = typeof window !== "undefined" ? window.location.host : "";
  return (
    <>
      <PageHead title="Protocols" description="What a client can connect with today, and what is still on the roadmap — shown honestly rather than hidden." />
      <div className="grid grid gap-3.5 grid-cols-[repeat(auto-fill,_minmax(220px,_1fr))] [&.wide]:grid-cols-[repeat(auto-fill,_minmax(320px,_1fr))] [&.two]:grid-cols-2 [&.three]:grid-cols-3 max-wide:[&.three.insights]:grid-cols-2 max-navigation:[&.two]:grid-cols-1 max-navigation:[&.three]:grid-cols-1 max-navigation:[&.three.insights]:grid-cols-1 [&:has(>_.tile)]:grid [&:has(>_.tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.tile)]:gap-0 [&:has(>_.tile)]:[border:1px_solid_var(--line)] [&:has(>_.tile)]:rounded-sm [&:has(>_.tile)]:overflow-hidden [&:has(>_.tile)]:mb-4 [&:has(>_.stat-tile)]:grid [&:has(>_.stat-tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.stat-tile)]:gap-0 [&:has(>_.stat-tile)]:[border:1px_solid_var(--line)] [&:has(>_.stat-tile)]:rounded-sm [&:has(>_.stat-tile)]:overflow-hidden [&:has(>_.stat-tile)]:mb-4 wide">
        {LIVE_PROTOCOLS.map((p) => (
          <Card
            key={p.key}
            title={<span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 10 }}><ProtocolIcon icon={p.icon} tone={p.tone} />{p.label}</span>}
            actions={<Badge tone="green">live</Badge>}
          >
            <p className="muted text-muted" style={{ marginTop: 0 }}>{p.about}</p>
            <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 6, margin: "0 0 12px" }}>
              {p.traits.map((t) => <Badge key={t}>{t}</Badge>)}
            </div>
            <CopyText text={p.snippet(host)} label={`${p.label} endpoint`} />
          </Card>
        ))}
        {UNAVAILABLE.map((p) => (
          <Card
            key={p.key}
            className="dimmed"
            title={<span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 10 }}><ProtocolIcon icon={p.icon} /><span className="muted text-muted">{p.label}</span></span>}
            actions={<Badge>not yet available</Badge>}
          >
            <p className="muted text-muted" style={{ margin: 0 }}>{p.about} Not built yet — shown here rather than hidden, so the gap is honest about where it is.</p>
          </Card>
        ))}
      </div>
    </>
  );
}

// ── subscriptions: database changes + webhook fan-out ────────────────────

export function Subscriptions({ env, onWatch }) {
  const resources = useApi(envPath(env, "/resources"));
  const webhooks = useApi(envPath(env, "/webhooks"));
  const [run, busy] = useAction();
  const [newHook, setNewHook] = useState(null);
  const toggleRealtime = async (name, value) => {
    const path = envPath(env, `/resources/${name}`);
    const done = await run(async () => {
      const current = await get(path);
      return put(path, { ...editable(current), realtime: value });
    }, value ? `Broadcasting changes on resource:${name}` : "Stopped broadcasting changes");
    if (done) resources.reload();
  };
  const realtimeHooks = (webhooks.data?.data || []).filter((w) => (w.events || []).some((e) => e === "realtime.message" || e === "realtime.*" || e === "*"));
  return (
    <>
      <PageHead title="Subscriptions" description="Two ways something outside a channel finds out about an event: database-change broadcasting, and webhooks that fire on every publish." />
      <Card icon="database" flush title="Database changes">
        <Loading state={resources} empty="No resources defined yet.">
          {(data) => (
            <Table
              rows={data.data || []}
              columns={[
                { label: "Resource", render: (r) => <code>{r.name}</code> },
                { label: "Channel", render: (r) => <code className="faint text-faint">resource:{r.name}</code> },
                { label: "Broadcasting", render: (r) => <span onClick={(e) => e.stopPropagation()}><Switch size="sm" checked={!!r.realtime} disabled={busy} onChange={(v) => toggleRealtime(r.name, v)} /></span> },
                { label: "", render: (r) => (r.realtime ? <Button size="sm" onClick={() => onWatch(`resource:${r.name}`)}><Icon name="eye" size={13} />Watch</Button> : null) },
              ]}
            />
          )}
        </Loading>
      </Card>
      <Card icon="webhooks" flush title="Webhook subscriptions" actions={<Button variant="primary" size="sm" onClick={() => setNewHook({ name: "", url: "" })}><Icon name="plus" />New webhook</Button>}>
        <Loading state={webhooks} empty="No webhooks yet.">
          {() => (
            realtimeHooks.length === 0 ? (
              <EmptyState icon="webhooks" title="No webhook is subscribed to realtime events">
                Create one pointed at <code>realtime.message</code> to get every publish delivered to a URL — useful for logging, billing, or systems that cannot hold a socket open.
              </EmptyState>
            ) : (
              <Table
                rows={realtimeHooks}
                columns={[
                  { label: "Name", key: "name" },
                  { label: "URL", render: (w) => <code className="small text-ui-sm">{w.url}</code> },
                  { label: "Events", render: (w) => (w.events || []).map((e) => <Badge key={e}>{e}</Badge>) },
                  { label: "Enabled", render: (w) => (w.enabled ? <Badge tone="green">on</Badge> : <Badge>off</Badge>) },
                ]}
              />
            )
          )}
        </Loading>
      </Card>
      <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: "-4px 2px 0" }}>Full control (retries, deliveries, secrets) lives under <Link className="link-more inline-flex items-center gap-0.5 text-ui-sm font-semibold text-muted [&:hover]:text-text" href={envHref(env, "webhooks")}>Webhooks</Link>.</p>
      {newHook && (
        <Modal title="New webhook subscription" onClose={() => setNewHook(null)} footer={<><Button onClick={() => setNewHook(null)}>Cancel</Button><Button variant="primary" disabled={busy || !newHook.name || !newHook.url} onClick={async () => { if (await run(() => post(envPath(env, "/webhooks"), { name: newHook.name, url: newHook.url, events: ["realtime.message"] }), "Webhook created")) { setNewHook(null); webhooks.reload(); } }}>Create</Button></>}>
          <Field label="Name"><input autoFocus value={newHook.name} onChange={(e) => setNewHook({ ...newHook, name: e.target.value })} placeholder="realtime-to-logs" /></Field>
          <Field label="URL"><input value={newHook.url} onChange={(e) => setNewHook({ ...newHook, url: e.target.value })} placeholder="https://example.com/hooks/realtime" /></Field>
          <p className="hint text-xs font-book text-muted leading-[1.45]">Subscribed to <code>realtime.message</code> — fired on every publish, on every channel.</p>
        </Modal>
      )}
    </>
  );
}

// ── playground ────────────────────────────────────────────────────────────

/** Watched channels live in the page, not in a modal: a small sidebar on the left picks which one
 *  the main panel shows. ``initialChannel`` arrives from a "Watch" button elsewhere in the workspace. */
export function Playground({ env, socket, initialChannel, onConsumedInitial }) {
  const [channels, setChannels] = useState([]);
  const [active, setActive] = useState(null);
  const [target, setTarget] = useState("");

  useEffect(() => {
    if (!initialChannel) return;
    setChannels((cs) => (cs.includes(initialChannel) ? cs : [...cs, initialChannel]));
    setActive(initialChannel);
    onConsumedInitial?.();
    // onConsumedInitial is a setter from the parent; only initialChannel should retrigger this
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialChannel]);

  const watch = (name) => {
    const clean = name.trim();
    if (!clean) return;
    setChannels((cs) => (cs.includes(clean) ? cs : [...cs, clean]));
    setActive(clean);
    setTarget("");
  };
  const stopWatching = (name) => {
    setChannels((cs) => cs.filter((c) => c !== name));
    setActive((current) => (current === name ? null : current));
  };

  return (
    <div className="rtp-shell h-full flex flex-col min-h-0">
      <div className="rtp-topbar flex items-center gap-3 p-[14px_20px] [border-bottom:1px_solid_var(--line)] shrink-0 [&_b]:text-[14.5px]">
        <b>Playground</b>
        <span className="faint text-faint small text-ui-sm grow flex-1 min-w-0">A real subscription over the same socket Studio itself uses — not simulated data.</span>
        <ConnectionBadge status={socket.status} />
      </div>
      <div className="rtp-body flex flex-1 min-h-0 bg-surface">
        <aside className="rtp-side w-[216px] shrink-0 flex flex-col [border-right:1px_solid_var(--line)] bg-sunken">
          <form className="rtp-watch-form flex items-center gap-2 m-2.5 p-[0_10px] [border:1px_solid_var(--line-2)] rounded-sm bg-surface text-faint [&_input]:flex-1 [&_input]:min-w-0 [&_input]:border-0 [&_input]:outline-none [&_input]:shadow-none [&_input]:bg-transparent [&_input]:p-[8px_0] [&_input]:text-ui [&:focus-within]:border-brand" onSubmit={(e) => { e.preventDefault(); watch(target); }}>
            <Icon name="eye" size={14} />
            <input placeholder="channel:name — Enter to watch" value={target} onChange={(e) => setTarget(e.target.value)} />
          </form>
          <div className="rtp-side-list flex-1 overflow-y-auto p-[0_6px_6px] flex flex-col gap-0.5">
            {channels.length === 0 && <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ padding: "4px 10px" }}>Watch a channel to start.</p>}
            {channels.map((c) => (
              <button key={c} type="button" className={`rtp-side-item flex items-center gap-2 w-full p-[8px_9px] border-0 rounded-sm bg-none text-left cursor-pointer text-ui-sm text-text [&_code]:overflow-hidden [&_code]:text-ellipsis [&_code]:whitespace-nowrap [&_code]:bg-none [&_code]:p-0 [&:hover]:bg-surface [&.active]:bg-lavender [&.active]:text-pastel-ink [&.active_code]:text-inherit ${c === active ? "active" : ""}`} onClick={() => setActive(c)}>
                <Icon name="layers" size={13} className="faint text-faint" />
                <code className="grow flex-1 min-w-0">{c}</code>
                <span className="rtp-remove flex opacity-[0.55] rounded-[4px] p-0.5 [&:hover]:opacity-100 [&:hover]:bg-[rgba(0,_0,_0,_0.08)]" role="button" aria-label={`Stop watching ${c}`} onClick={(e) => { e.stopPropagation(); stopWatching(c); }}><Icon name="x" size={12} /></span>
              </button>
            ))}
          </div>
        </aside>
        <div className="rtp-main flex-1 min-w-0 min-h-0 flex [&_>_.empty-state]:m-[auto]">
          {active ? <ChannelFeed key={active} channel={active} socket={socket} /> : (
            <EmptyState icon="play" title="Nothing watched yet">Add a channel on the left, or use a "Watch" button elsewhere in the workspace, to see it live here.</EmptyState>
          )}
        </div>
      </div>
    </div>
  );
}

/** One channel's live console: a real subscription over the shared socket (messages arrive as they're
 *  published, presence updates live). Each message is formatted by default; its own toggle reveals the raw wire message. */
function ChannelFeed({ channel, socket }) {
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
    <div className="rtp-channel flex-1 min-w-0 flex flex-col p-4 gap-3.5 min-h-0">
      <div className="rtp-channel-head flex items-center justify-between gap-2.5">
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 8 }}>
          <code className="rtp-channel-name text-base">{channel}</code>
          <Badge tone={joined ? "green" : error ? "red" : undefined}>{joined ? "subscribed" : error ? "refused" : "subscribing…"}</Badge>
        </div>
      </div>
      {error && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{error}</div>}

      <div className="rtp-channel-cols flex gap-5 flex-1 min-h-0">
        <div className="rtp-channel-main flex-1 min-w-0 flex flex-col gap-3.5 min-h-0">
          <div className="rtp-feed flex-1 min-h-[160px] overflow-y-auto flex flex-col gap-2 p-0.5">
            {feed.length === 0 && <p className="faint text-faint" style={{ padding: 16, textAlign: "center" }}>{joined ? "Nothing published yet. Send one from the right, or from a client." : "Waiting to subscribe…"}</p>}
            {feed.map((m, i) => <MessageCard key={m.id || i} message={m} />)}
            <div ref={bottomRef} />
          </div>

          <div className="rtp-presence flex items-center gap-2.5 flex-wrap pt-2.5 [border-top:1px_solid_var(--line)] shrink-0">
            <b className="small text-ui-sm">Presence {members != null && <span className="faint text-faint">({members.length})</span>}</b>
            {members == null ? (
              <span className="faint text-faint small text-ui-sm">off, or not loaded yet</span>
            ) : members.length === 0 ? (
              <span className="faint text-faint small text-ui-sm">nobody present</span>
            ) : (
              <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 6 }}>
                {members.map((m) => <Badge key={m.connection_id || m.id || m.user_id}>{m.user_id || m.connection_id || "anon"}</Badge>)}
              </div>
            )}
          </div>
        </div>

        <PublishBox channel={channel} socket={socket} />
      </div>
    </div>
  );
}

const TRUNCATE_AT = 90;

/** One key/value line, truncated past a length with its own "show more" — long values (a big JSON blob, a
 *  long string field) stay out of the way of the rest of the message until asked for. */
function KVRow({ k, v }) {
  const [expanded, setExpanded] = useState(false);
  const text = v !== null && typeof v === "object" ? JSON.stringify(v) : String(v);
  const long = text.length > TRUNCATE_AT;
  return (
    <div className="rtp-kv-row contents">
      <span className="rtp-kv-key text-muted font-mono whitespace-nowrap overflow-hidden text-ellipsis pt-px" title={k}>{k}</span>
      <span className="rtp-kv-val font-mono min-w-0 wrap-break-word leading-[1.5]">
        {expanded || !long ? text : `${text.slice(0, TRUNCATE_AT)}…`}
        {long && <button type="button" className="rtp-more ml-1.5 border-0 bg-none p-0 text-2xs font-semibold text-brand cursor-pointer [&:hover]:underline" onClick={() => setExpanded((v) => !v)}>{expanded ? "less" : "more"}</button>}
      </span>
    </div>
  );
}

/** Pretty JSON that collapses past a few lines, with a "show more" to see the rest. */
function JsonBlock({ value }) {
  const [expanded, setExpanded] = useState(false);
  const text = JSON.stringify(value, null, 2);
  const long = text.split("\n").length > 7 || text.length > 400;
  return (
    <div>
      <pre className={`rtp-json text-caption m-0 whitespace-pre-wrap wrap-break-word text-muted max-w-[100%] [&.clamped]:max-h-[110px] [&.clamped]:overflow-hidden ${!expanded && long ? "clamped" : ""}`}>{text}</pre>
      {long && <button type="button" className="rtp-more ml-1.5 border-0 bg-none p-0 text-2xs font-semibold text-brand cursor-pointer [&:hover]:underline" onClick={() => setExpanded((v) => !v)}>{expanded ? "Show less" : "Show more"}</button>}
    </div>
  );
}

/** A plain object's keys and values, read-only, one per line — the "fancy" default view. Anything
 *  that isn't a simple object (array, scalar) falls back to pretty JSON. */
function PrettyPayload({ payload }) {
  if (payload === null || payload === undefined) return <span className="faint text-faint small text-ui-sm">—</span>;
  if (typeof payload !== "object") return <code className="rtp-scalar text-ui-sm">{JSON.stringify(payload)}</code>;
  if (Array.isArray(payload) || Object.keys(payload).length === 0) {
    return <JsonBlock value={payload} />;
  }
  return (
    <div className="rtp-kv grid grid-cols-[minmax(60px,_140px)_minmax(0,_1fr)] gap-[7px_14px] text-ui-sm max-w-[100%]">
      {Object.entries(payload).map(([k, v]) => <KVRow key={k} k={k} v={v} />)}
    </div>
  );
}

const EVENT_ICONS = { message: "send", created: "plus", updated: "refresh", deleted: "trash", notification: "bell" };
const eventIcon = (event) => EVENT_ICONS[event] || "layers";

/** A small pill that flips one message between its formatted view and the raw wire message. */
function RawToggle({ raw, onChange }) {
  return (
    <div className="rtp-rawtoggle flex items-center gap-px p-0.5 rounded-full bg-sunken [border:1px_solid_var(--line-2)] shrink-0 [&_button]:flex [&_button]:items-center [&_button]:justify-center [&_button]:w-5 [&_button]:h-[18px] [&_button]:border-0 [&_button]:rounded-full [&_button]:bg-none [&_button]:text-faint [&_button]:cursor-pointer [&_button]:transition-[background_0.12s_ease,_color_0.12s_ease] [&_button:hover]:text-text [&_button.on]:bg-surface [&_button.on]:text-text [&_button.on]:[box-shadow:0_1px_2px_rgba(0,_0,_0,_0.08)]" role="group" aria-label="Payload view">
      <button type="button" className={!raw ? "on" : ""} aria-pressed={!raw} title="Formatted" onClick={() => onChange(false)}><Icon name="sparkle" size={12} /></button>
      <button type="button" className={raw ? "on" : ""} aria-pressed={raw} title="Raw" onClick={() => onChange(true)}><Icon name="code" size={12} /></button>
    </div>
  );
}

function MessageCard({ message }) {
  const [raw, setRaw] = useState(false);
  return (
    <div className="rtp-msg bg-sunken [border:1px_solid_var(--line)] rounded-sm p-[10px_12px] min-w-0 max-w-[100%]">
      <div className="rtp-msg-head flex items-center gap-2 mb-2">
        <span className="rtp-msg-icon inline-flex items-center justify-center text-brand shrink-0"><Icon name={eventIcon(message.event)} size={16} /></span>
        <Badge tone="lavender">{message.event || "message"}</Badge>
        <span className="rtp-msg-meta text-caption text-muted text-right grow flex-1 min-w-0">{message.from ? `from ${message.from}` : "server"} · {when(message.sent_at)}</span>
        <RawToggle raw={raw} onChange={setRaw} />
      </div>
      {raw ? <JsonBlock value={message} /> : <PrettyPayload payload={message.payload} />}
    </div>
  );
}

const EVENT_PRESETS = ["message", "created", "updated", "deleted", "notification"];

function PublishBox({ channel, socket }) {
  const [event, setEvent] = useState("message");
  const [customEvent, setCustomEvent] = useState("");
  const [payload, setPayload] = useState({ text: "hello from Studio" });
  const [run, busy] = useAction();
  const [last, setLast] = useState(null);
  const effectiveEvent = event === "__custom" ? customEvent : event;
  return (
    <div className="rtp-publish w-[260px] shrink-0 pl-5 [border-left:1px_solid_var(--line)] flex flex-col">
      <b className="small text-ui-sm">Publish</b>
      <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: "2px 0 10px" }}>Sent through the same authorization and delivery path a real client uses.</p>
      <Field label="Event">
        <select value={event} onChange={(e) => setEvent(e.target.value)}>
          {EVENT_PRESETS.map((v) => <option key={v} value={v}>{v}</option>)}
          <option value="__custom">Custom…</option>
        </select>
      </Field>
      {event === "__custom" && <input className="mono font-mono text-ui-sm" style={{ marginTop: 8 }} value={customEvent} onChange={(e) => setCustomEvent(e.target.value)} placeholder="event name" autoFocus />}
      <div style={{ marginTop: 10 }}><Field label="Payload"><JsonInput value={payload} onChange={setPayload} rows={5} /></Field></div>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ justifyContent: "flex-end", marginTop: 10 }}>
        <Button variant="primary" disabled={busy || !effectiveEvent} onClick={() => run(async () => { const ack = await socket.publish(channel, effectiveEvent, payload); setLast(ack); }, "Published")}><Icon name="send" size={13} />Send</Button>
      </div>
      {last?.ok === false && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error" style={{ marginTop: 10 }}>{last.reason || last.error}</div>}
    </div>
  );
}
