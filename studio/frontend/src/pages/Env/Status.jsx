import { usePage } from "@inertiajs/react";
import { useEffect, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, CopyText, Field, Loading, Modal, PageHead, Section, Switch, Table, TagInput, when, useAction } from "../../components/ui";
import { del, envPath, post, put, useApi } from "../../lib/api";

const SOURCES = {
  gateway: "Your API",
  auth: "Sign-in",
  realtime: "Realtime",
  workers: "Background jobs",
  manual: "Set by hand (only incidents change it)",
};
const TONES = { operational: "green", degraded: "yellow", outage: "red" };
const STAGES = ["investigating", "identified", "monitoring", "resolved"];
const IMPACTS = ["none", "minor", "major", "critical"];

export default function Status({ env }) {
  const { gateway_url: gatewayUrl } = usePage().props;
  const base = envPath(env);
  const page = useApi(`${base}/status-page`);
  const incidents = useApi(`${base}/incidents`);
  const [draft, setDraft] = useState(null);
  const [opening, setOpening] = useState(null);
  const [updating, setUpdating] = useState(null);
  const [run, busy] = useAction();

  useEffect(() => {
    if (page.data && !draft) setDraft(page.data.settings);
  }, [page.data]);

  const set = (patch) => setDraft({ ...draft, ...patch });
  const setComponent = (i, patch) => set({ components: draft.components.map((c, n) => (n === i ? { ...c, ...patch } : c)) });
  const publicUrl = `${(gatewayUrl || "").replace(/\/$/, "")}/status/${env}`;

  const save = async () => {
    if (await run(() => put(`${base}/status-page`, draft), "Status page saved")) page.reload();
  };

  return (
    <Layout title="Status page">
      <PageHead
        title="Status page"
        description="A public page showing whether this project is up, with 90 days of history and the incidents you write. Nothing private is on it."
        actions={<Button variant="primary" disabled={busy || !draft} onClick={save}>Save</Button>}
      />
      <Loading state={page}>
        {(data) => draft && (
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 max-w-[920px] mx-auto">
            <Section icon="terminal" title="Page" description="Turn it on and the gateway starts serving it. Sampling begins as soon as it is enabled.">
              <div className="flex flex-col gap-4">
                <Switch checked={draft.enabled} onChange={(enabled) => set({ enabled })} label="Publish the status page" hint={draft.enabled ? <>Live at <CopyText text={publicUrl} label={publicUrl} /> and <code>{publicUrl}.json</code></> : "Hidden: the address returns 404."} />
                <div className="grid2 grid grid-cols-2 gap-[14px_16px] max-tablet:grid-cols-1">
                  <Field label="Title"><input value={draft.title} onChange={(e) => set({ title: e.target.value })} /></Field>
                  <Field label="Support link" optional><input value={draft.contact_url} onChange={(e) => set({ contact_url: e.target.value })} placeholder="https://example.com/support" /></Field>
                </div>
                <Field label="Description" optional><input value={draft.description} onChange={(e) => set({ description: e.target.value })} placeholder="How Acme is doing right now" /></Field>
              </div>
            </Section>
            <Section icon="layers" title="Components" description="What visitors see. Probed components are checked every five minutes; a state comes from the latest checks.">
              <Table
                rows={draft.components.map((c, i) => ({ ...c, i }))}
                empty="No components."
                columns={[
                  { label: "Name", render: (c) => <input value={c.name} onChange={(e) => setComponent(c.i, { name: e.target.value })} /> },
                  { label: "Watches", render: (c) => (
                    <select value={c.source} onChange={(e) => setComponent(c.i, { source: e.target.value })}>
                      {data.sources.map((s) => <option key={s} value={s}>{SOURCES[s]}</option>)}
                    </select>
                  ) },
                  { label: "Now", render: (c) => {
                    const live = data.preview?.components.find((x) => x.name === c.name);
                    return live ? <Badge tone={TONES[live.status]}>{live.status}{live.uptime != null ? ` · ${live.uptime}%` : ""}</Badge> : <span className="faint text-faint">—</span>;
                  } },
                  { label: "", render: (c) => <Button size="sm" variant="danger" onClick={() => set({ components: draft.components.filter((_, n) => n !== c.i) })}>Remove</Button> },
                ]}
              />
              <div className="self-start">
                <Button onClick={() => set({ components: [...draft.components, { name: "New component", source: "manual", description: "" }] })}>Add component</Button>
              </div>
            </Section>
            <Section icon="events" title="Incidents" description="Write what is happening. An open incident marks the components it names as degraded or down until you resolve it."
              actions={<Button variant="primary" onClick={() => setOpening({ title: "", status: "investigating", impact: "minor", components: [], message: "" })}>Open incident</Button>}>
              <Loading state={incidents} empty="No incidents. That is the goal.">
                {(list) => (
                  <Table
                    rows={list.data}
                    columns={[
                      { label: "Incident", render: (i) => <strong>{i.title}</strong> },
                      { label: "State", render: (i) => <Badge tone={i.status === "resolved" ? "green" : "yellow"}>{i.status}</Badge> },
                      { label: "Impact", key: "impact" },
                      { label: "Affects", render: (i) => (i.components?.length ? i.components.join(", ") : "everything") },
                      { label: "Started", render: (i) => when(i.created_at) },
                      { label: "", render: (i) => <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                        <Button size="sm" onClick={() => setUpdating(i)}>Post update</Button>
                        <Button size="sm" variant="danger" onClick={async () => { if (confirm("Delete this incident and its updates?") && await run(() => del(`${base}/incidents/${i.id}`), "Deleted")) { incidents.reload(); page.reload(); } }}>Delete</Button>
                      </div> },
                    ]}
                  />
                )}
              </Loading>
            </Section>
          </div>
        )}
      </Loading>
      {opening && <IncidentSheet base={base} draft={opening} names={(draft?.components || []).map((c) => c.name)} onClose={() => setOpening(null)} onDone={() => { setOpening(null); incidents.reload(); page.reload(); }} />}
      {updating && <UpdateSheet base={base} incident={updating} onClose={() => setUpdating(null)} onDone={() => { setUpdating(null); incidents.reload(); page.reload(); }} />}
    </Layout>
  );
}

function IncidentSheet({ base, draft, names, onClose, onDone }) {
  const [data, setData] = useState(draft);
  const [run, busy] = useAction();
  return (
    <Modal title="Open incident" onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.title} onClick={async () => { if (await run(() => post(`${base}/incidents`, data), "Incident opened")) onDone(); }}>Publish</Button>}>
      <Field label="Title"><input value={data.title} onChange={(e) => setData({ ...data, title: e.target.value })} placeholder="Sign-in is slow" /></Field>
      <div className="grid2 grid grid-cols-2 gap-[14px_16px] max-tablet:grid-cols-1">
        <Field label="Stage"><select value={data.status} onChange={(e) => setData({ ...data, status: e.target.value })}>{STAGES.map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Impact"><select value={data.impact} onChange={(e) => setData({ ...data, impact: e.target.value })}>{IMPACTS.map((s) => <option key={s}>{s}</option>)}</select></Field>
      </div>
      <Field label="Components affected" hint="Leave empty for everything." optional><TagInput value={data.components} onChange={(components) => setData({ ...data, components })} suggestions={names} /></Field>
      <Field label="Message" optional><textarea rows={3} value={data.message} onChange={(e) => setData({ ...data, message: e.target.value })} placeholder="We are looking into this." /></Field>
    </Modal>
  );
}

function UpdateSheet({ base, incident, onClose, onDone }) {
  const [data, setData] = useState({ status: incident.status === "resolved" ? "monitoring" : incident.status, message: "", impact: incident.impact });
  const [run, busy] = useAction();
  return (
    <Modal title={`Update: ${incident.title}`} onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.message} onClick={async () => { if (await run(() => post(`${base}/incidents/${incident.id}/updates`, data), "Update posted")) onDone(); }}>Post</Button>}>
      <div className="grid2 grid grid-cols-2 gap-[14px_16px] max-tablet:grid-cols-1">
        <Field label="Stage"><select value={data.status} onChange={(e) => setData({ ...data, status: e.target.value })}>{STAGES.map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Impact"><select value={data.impact} onChange={(e) => setData({ ...data, impact: e.target.value })}>{IMPACTS.map((s) => <option key={s}>{s}</option>)}</select></Field>
      </div>
      <Field label="Message"><textarea rows={3} value={data.message} onChange={(e) => setData({ ...data, message: e.target.value })} /></Field>
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ marginTop: 10 }}>
        {[...(incident.updates || [])].reverse().map((u, n) => <div key={n} className="muted text-muted small text-ui-sm"><b>{u.status}</b> · {when(u.at)} — {u.message}</div>)}
      </div>
    </Modal>
  );
}
