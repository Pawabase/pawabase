import { Link } from "@inertiajs/react";
import { useState } from "react";
import Layout, { envHref } from "../../components/Layout";
import { Badge, Button, Card, CopyText, EmptyState, Field, Loading, Modal, PageHead, Switch, Table, Tabs, TagInput, when, useAction } from "../../components/ui";
import { del, envPath, patch, post, put, useApi } from "../../lib/api";

const STATUS_TONES = { verified: "green", pending: "yellow", failed: "red" };

export default function Networking({ env }) {
  const base = envPath(env);
  const [tab, setTab] = useState("domains");
  return (
    <Layout title="Networking">
      <PageHead title="Networking" description="Which addresses this environment answers on, and who is let through to it." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "domains", label: "Domains" }, { value: "firewall", label: "Firewall" }]} />
      <div className="stack lg" style={{ marginTop: 16 }}>
        {tab === "domains" ? <Domains base={base} /> : <Firewall base={base} />}
        <Card title="Other network controls">
          <p className="muted">
            Allowed origins (CORS), the secret-key IP allowlist and maintenance mode live in <Link href={envHref(env, "settings")}>Settings</Link>. Each API key can also be limited to IP ranges and routes under <Link href={envHref(env, "keys")}>API keys</Link>.
          </p>
        </Card>
      </div>
    </Layout>
  );
}

function Domains({ base }) {
  const list = useApi(`${base}/domains`);
  const [adding, setAdding] = useState(false);
  const [run, busy] = useAction();
  return (
    <Card title="Custom domains" actions={<Button variant="primary" onClick={() => setAdding(true)}>Add domain</Button>} flush>
      <Loading state={list} empty={<EmptyState icon="link" title="No custom domains" action={<Button variant="primary" onClick={() => setAdding(true)}>Add domain</Button>}>Serve this environment's API from your own hostname, such as api.example.com.</EmptyState>}>
        {(data) => (
          <Table
            rows={data.data}
            columns={[
              { label: "Hostname", render: (d) => <code>{d.hostname}</code> },
              { label: "Status", render: (d) => <Badge tone={STATUS_TONES[d.status]}>{d.status}</Badge> },
              { label: "DNS record", render: (d) => d.status === "verified" ? <span className="muted small">verified {when(d.verified_at)}</span> : (
                <div className="stack" style={{ gap: 4 }}>
                  <span className="small muted">Add a {d.record.type} record named</span>
                  <CopyText text={d.record.name} />
                  <span className="small muted">with the value</span>
                  <CopyText text={d.record.value} />
                  {d.last_error && <span className="small" style={{ color: "var(--red, #c4453a)" }}>{d.last_error}</span>}
                </div>
              ) },
              { label: "", render: (d) => <div className="row">
                <Button size="sm" disabled={busy} onClick={async () => { if (await run(() => post(`${base}/domains/${d.id}/verify`))) list.reload(); }}>{d.status === "verified" ? "Re-check" : "Check now"}</Button>
                <Button size="sm" variant="danger" onClick={async () => { if (confirm(`Remove ${d.hostname}?`) && await run(() => del(`${base}/domains/${d.id}`), "Removed")) list.reload(); }}>Remove</Button>
              </div> },
            ]}
          />
        )}
      </Loading>
      <p className="hint" style={{ padding: "12px 16px" }}>
        Verifying proves you own the hostname. Once verified, point it at your gateway with a CNAME or A record. Pawabase does not issue certificates: terminate TLS for the hostname at the proxy or CDN in front of the gateway.
      </p>
      {adding && <AddDomain base={base} onClose={() => setAdding(false)} onDone={() => { setAdding(false); list.reload(); }} />}
    </Card>
  );
}

function AddDomain({ base, onClose, onDone }) {
  const [hostname, setHostname] = useState("");
  const [run, busy] = useAction();
  return (
    <Modal title="Add a domain" onClose={onClose} footer={<Button variant="primary" disabled={busy || !hostname} onClick={async () => { if (await run(() => post(`${base}/domains`, { hostname }), "Domain added: add the DNS record to verify it")) onDone(); }}>Add</Button>}>
      <Field label="Hostname" hint="Without https://, like api.example.com"><input autoFocus value={hostname} onChange={(e) => setHostname(e.target.value)} placeholder="api.example.com" /></Field>
    </Modal>
  );
}

const MATCH_LABELS = { ips: "IPs", paths: "Paths", methods: "Methods", user_agents: "User agents" };
const summary = (match) => Object.entries(match || {}).map(([k, v]) => `${MATCH_LABELS[k] || k}: ${v.join(", ")}`).join(" · ");

function Firewall({ base }) {
  const list = useApi(`${base}/firewall`);
  const [editing, setEditing] = useState(null);
  const [testing, setTesting] = useState(false);
  const [run] = useAction();
  const move = async (rows, index, by) => {
    const ids = rows.map((r) => r.id);
    const [item] = ids.splice(index, 1);
    ids.splice(index + by, 0, item);
    if (await run(() => put(`${base}/firewall-order`, { ids }))) list.reload();
  };
  return (
    <Card title="Firewall rules" actions={<div className="row"><Button onClick={() => setTesting(true)}>Test a request</Button><Button variant="primary" onClick={() => setEditing({ name: "", action: "block", enabled: true, match: {}, note: "" })}>Add rule</Button></div>} flush>
      <Loading state={list} empty={<EmptyState icon="policies" title="No firewall rules" action={<Button variant="primary" onClick={() => setEditing({ name: "", action: "block", enabled: true, match: {}, note: "" })}>Add rule</Button>}>Rules run at the gateway before a request reaches anything else. The first rule that matches decides; requests no rule matches go through.</EmptyState>}>
        {(data) => (
          <Table
            rows={data.data}
            columns={[
              { label: "#", render: (r) => <span className="faint">{data.data.indexOf(r) + 1}</span> },
              { label: "Rule", render: (r) => <div><strong>{r.name}</strong>{r.note && <div className="faint small">{r.note}</div>}</div> },
              { label: "Action", render: (r) => <Badge tone={r.action === "allow" ? "green" : "red"}>{r.action}</Badge> },
              { label: "When", render: (r) => <span className="muted small">{summary(r.match)}</span> },
              { label: "On", render: (r) => <Switch checked={r.enabled} onChange={async (enabled) => { if (await run(() => patch(`${base}/firewall/${r.id}`, { enabled }))) list.reload(); }} /> },
              { label: "", render: (r) => { const i = data.data.indexOf(r); return (
                <div className="row">
                  <Button size="sm" disabled={i === 0} onClick={() => move(data.data, i, -1)}>↑</Button>
                  <Button size="sm" disabled={i === data.data.length - 1} onClick={() => move(data.data, i, 1)}>↓</Button>
                  <Button size="sm" onClick={() => setEditing(r)}>Edit</Button>
                  <Button size="sm" variant="danger" onClick={async () => { if (confirm(`Delete "${r.name}"?`) && await run(() => del(`${base}/firewall/${r.id}`), "Deleted")) list.reload(); }}>Delete</Button>
                </div>
              ); } },
            ]}
          />
        )}
      </Loading>
      <p className="hint" style={{ padding: "12px 16px" }}>Rules apply to requests that carry an API key, and take effect within about 30 seconds.</p>
      {editing && <RuleSheet base={base} rule={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); list.reload(); }} />}
      {testing && <TestSheet base={base} onClose={() => setTesting(false)} />}
    </Card>
  );
}

function RuleSheet({ base, rule, onClose, onDone }) {
  const [data, setData] = useState({ ...rule, match: { ips: [], paths: [], methods: [], user_agents: [], ...rule.match } });
  const [run, busy] = useAction();
  const set = (key) => (value) => setData({ ...data, match: { ...data.match, [key]: value } });
  const submit = async () => {
    const body = { name: data.name, action: data.action, enabled: data.enabled, note: data.note, match: data.match };
    const ok = await run(() => (rule.id ? patch(`${base}/firewall/${rule.id}`, body) : post(`${base}/firewall`, body)), "Rule saved");
    if (ok) onDone();
  };
  return (
    <Modal title={rule.id ? "Edit rule" : "Add rule"} onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.name} onClick={submit}>Save</Button>}>
      <Field label="Name"><input value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} placeholder="Block the admin API from outside the office" /></Field>
      <Field label="Action"><select value={data.action} onChange={(e) => setData({ ...data, action: e.target.value })}><option value="block">Block</option><option value="allow">Allow (stop checking later rules)</option></select></Field>
      <p className="hint">A request matches when it fits every box you fill in. Within a box, any one value is enough.</p>
      <Field label="IP addresses" hint="Addresses or CIDR ranges, like 10.0.0.0/8" optional><TagInput value={data.match.ips} onChange={set("ips")} /></Field>
      <Field label="Paths" hint="Globs, like /rest/v1/admin/*" optional><TagInput value={data.match.paths} onChange={set("paths")} /></Field>
      <Field label="Methods" optional><TagInput value={data.match.methods} onChange={set("methods")} suggestions={["GET", "POST", "PUT", "PATCH", "DELETE"]} /></Field>
      <Field label="User agents" hint="Matches if the header contains the text" optional><TagInput value={data.match.user_agents} onChange={set("user_agents")} /></Field>
      <Field label="Note" optional><input value={data.note} onChange={(e) => setData({ ...data, note: e.target.value })} /></Field>
    </Modal>
  );
}

function TestSheet({ base, onClose }) {
  const [probe, setProbe] = useState({ ip: "203.0.113.7", path: "/rest/v1/notes", method: "GET", user_agent: "" });
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  return (
    <Modal title="Test a request" onClose={onClose} footer={<Button variant="primary" disabled={busy || !probe.ip} onClick={async () => { const r = await run(() => post(`${base}/firewall-test`, probe)); if (r && r !== true) setResult(r); }}>Test</Button>}>
      <div className="grid2">
        <Field label="From IP"><input value={probe.ip} onChange={(e) => setProbe({ ...probe, ip: e.target.value })} /></Field>
        <Field label="Method"><input value={probe.method} onChange={(e) => setProbe({ ...probe, method: e.target.value })} /></Field>
      </div>
      <Field label="Path"><input value={probe.path} onChange={(e) => setProbe({ ...probe, path: e.target.value })} /></Field>
      <Field label="User agent" optional><input value={probe.user_agent} onChange={(e) => setProbe({ ...probe, user_agent: e.target.value })} /></Field>
      {result && <div className={`alert ${result.outcome === "allow" ? "info" : "error"}`}>{result.outcome === "allow" ? "Let through" : "Blocked"}{result.rule ? <> by rule <b>{result.rule.name}</b></> : " — no rule matches this request"}.</div>}
    </Modal>
  );
}
