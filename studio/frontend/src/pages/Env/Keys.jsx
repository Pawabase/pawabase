import { usePage } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, CopyText, Field, Loading, Modal, PageHead, Table, TagInput, useAction, when } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

// These are scopes enforced by the current API. TagInput still accepts custom
// values, so a key can be prepared for policy scopes too.
const API_KEY_SCOPES = [
  "resource:read",
  "resource:write",
  "routes:invoke",
  "functions:invoke",
];

export default function Keys({ env }) {
  const base = envPath(env, "/keys");
  const keys = useApi(base);
  const [creating, setCreating] = useState(false);
  const [revealed, setRevealed] = useState(null);
  const [run] = useAction();
  const { gateway_url } = usePage().props;
  return (
    <Layout title="API keys">
      <PageHead
        title="API keys"
        description="Clients send a key in the apikey header. Publishable keys act as anon and obey policies; secret keys act as the service and bypass them."
        actions={<Button variant="primary" onClick={() => setCreating(true)}>New key</Button>}
      />
      <HealthCheck gatewayUrl={gateway_url} env={env} />
      <Card flush>
        <Loading state={keys} empty="No keys.">
          {(data) => (
            <Table
              rows={data.data}
              columns={[
                { label: "Name", render: (k) => <b>{k.name}</b> },
                { label: "Role", render: (k) => <Badge tone={k.role === "secret" ? "yellow" : "blue"}>{k.role}</Badge> },
                { label: "Prefix", render: (k) => <code>{k.prefix}…</code> },
                { label: "Scopes", render: (k) => (k.scopes?.length ? k.scopes.join(", ") : "all") },
                { label: "Restrictions", render: (k) => [k.allowed_ips?.length && `IPs: ${k.allowed_ips.join(", ")}`, k.allowed_routes?.length && `Routes: ${k.allowed_routes.join(", ")}`].filter(Boolean).join(" · ") || "none" },
                { label: "Last used", render: (k) => when(k.last_used_at) },
                { label: "State", render: (k) => <Badge tone={k.active ? "green" : "red"}>{k.active ? "active" : "revoked"}</Badge> },
                { label: "", render: (k) => k.active && <span className="row" style={{ gap: 6 }}><Button size="sm" onClick={async () => { const hours = prompt(`Issue a replacement for ${k.name}. How many hours should the old key keep working? (0 ends it now)`, "24"); if (hours === null) return; const fresh = await run(() => post(`${base}/${k.id}/rotate`, { grace_hours: Number(hours) || 0 })); if (fresh && fresh !== true) { setRevealed(fresh); keys.reload(); } }}>Rotate</Button><Button size="sm" variant="danger" onClick={async () => { if (confirm(`Revoke ${k.name}? Clients using it stop working at once.`) && await run(() => post(`${base}/${k.id}/revoke`), "Key revoked")) keys.reload(); }}>Revoke</Button></span> },
              ]}
            />
          )}
        </Loading>
      </Card>
      {creating && <NewKey base={base} onClose={() => setCreating(false)} onCreated={(k) => { setCreating(false); setRevealed(k); keys.reload(); }} />}
      {revealed && (
        <Modal title="Your new key" onClose={() => setRevealed(null)} footer={<Button variant="primary" onClick={() => setRevealed(null)}>Done</Button>}>
          <div className="alert warn">Shown once. Copy it now.</div>
          <CopyText text={revealed.key} />
        </Modal>
      )}
    </Layout>
  );
}

function HealthCheck({ gatewayUrl, env }) {
  const url = gatewayUrl || "<gateway-url>";
  const health = `${url}/health/v1?environment=${env}`;
  return (
    <Card>
      <h3 style={{ marginTop: 0 }}>Health check</h3>
      <p className="muted" style={{ fontSize: 12.5, marginTop: -6 }}>
        Requires a key for this environment in the <code>apikey</code> header — a 200 proves the key resolves and the
        environment is up.
      </p>
      <CopyText text={health} />
    </Card>
  );
}

function NewKey({ base, onClose, onCreated }) {
  const [data, setData] = useState({ name: "", role: "publishable", scopes: [], allowed_ips: "", allowed_routes: "" });
  const [run, busy] = useAction();
  return (
    <Modal title="New API key" onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.name} onClick={async () => {
      const split = (value) => value.split(",").map((s) => s.trim()).filter(Boolean);
      const r = await run(() => post(base, { name: data.name, role: data.role, scopes: data.scopes, allowed_ips: split(data.allowed_ips), allowed_routes: split(data.allowed_routes) }));
      if (r) onCreated(r);
    }}>Create</Button>}>
      <Field label="Name"><input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} placeholder="web app" /></Field>
      <Field label="Role">
        <select value={data.role} onChange={(e) => setData({ ...data, role: e.target.value })}>
          <option value="publishable">publishable — browsers and apps</option>
          <option value="secret">secret — servers only</option>
        </select>
      </Field>
      <Field label="Scopes" hint="Click a scope to limit the key. Leave empty for unrestricted access within its role."><TagInput value={data.scopes} onChange={(scopes) => setData({ ...data, scopes })} suggestions={API_KEY_SCOPES} placeholder="Custom scope, then Enter" /></Field>
      <Field label="Allowed IP ranges" hint="Optional comma-separated IPs or CIDRs. The gateway rejects every other client."><input value={data.allowed_ips} onChange={(e) => setData({ ...data, allowed_ips: e.target.value })} placeholder="203.0.113.8, 10.0.0.0/8" /></Field>
      <Field label="Allowed routes" hint="Optional comma-separated gateway rules: GET /rest/v1/* or POST /functions/v1/*."><input value={data.allowed_routes} onChange={(e) => setData({ ...data, allowed_routes: e.target.value })} placeholder="GET /rest/v1/*" /></Field>
    </Modal>
  );
}
