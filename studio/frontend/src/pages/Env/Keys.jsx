import { usePage } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Choices, CopyText, Field, Loading, Modal, PageHead, Section, Table, useAction, when } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

/** A key's scopes in a few words: "Full access", or one chip per family with what it may do there. */
function ScopeSummary({ scopes, catalog }) {
  if (!scopes?.length) return <span className="text-muted">Full access</span>;
  const labels = Object.fromEntries((catalog?.families || []).map((f) => [f.id, f.label]));
  const by = {};
  for (const scope of scopes) {
    const [family, kind = "*"] = scope.split(":");
    by[family] = by[family] === "*" || by[family] === kind ? by[family] : by[family] ? "*" : kind;
  }
  const word = { read: "read", write: "write", "*": "read & write" };
  return <span className="flex flex-wrap gap-1">{Object.entries(by).map(([family, kind]) => <Badge key={family}>{scope(labels[family] || family, word[kind] || kind)}</Badge>)}</span>;
}
const scope = (label, kind) => `${label}: ${kind}`;

export default function Keys({ env }) {
  const base = envPath(env, "/keys");
  const keys = useApi(base);
  const catalog = useApi("/key-scopes").data;
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
      <Card bleed flush>
        <Loading state={keys} empty="No keys.">
          {(data) => (
            <Table
              rows={data.data}
              columns={[
                { label: "Name", render: (k) => <b>{k.name}</b> },
                { label: "Role", render: (k) => <Badge tone={k.role === "secret" ? "yellow" : "blue"}>{k.role}</Badge> },
                { label: "Prefix", render: (k) => <code>{k.prefix}…</code> },
                { label: "Scopes", render: (k) => <ScopeSummary scopes={k.scopes} catalog={catalog} /> },
                { label: "Restrictions", render: (k) => [k.allowed_ips?.length && `IPs: ${k.allowed_ips.join(", ")}`, k.allowed_routes?.length && `Routes: ${k.allowed_routes.join(", ")}`].filter(Boolean).join(" · ") || "none" },
                { label: "Last used", render: (k) => when(k.last_used_at) },
                { label: "State", render: (k) => <Badge tone={k.active ? "green" : "red"}>{k.active ? "active" : "revoked"}</Badge> },
                { label: "", render: (k) => k.active && <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}><Button size="sm" onClick={async () => { const hours = prompt(`Issue a replacement for ${k.name}. How many hours should the old key keep working? (0 ends it now)`, "24"); if (hours === null) return; const fresh = await run(() => post(`${base}/${k.id}/rotate`, { grace_hours: Number(hours) || 0 })); if (fresh && fresh !== true) { setRevealed(fresh); keys.reload(); } }}>Rotate</Button><Button size="sm" variant="danger" onClick={async () => { if (confirm(`Revoke ${k.name}? Clients using it stop working at once.`) && await run(() => post(`${base}/${k.id}/revoke`), "Key revoked")) keys.reload(); }}>Revoke</Button></span> },
              ]}
            />
          )}
        </Loading>
      </Card>
      {creating && <NewKey base={base} catalog={catalog} onClose={() => setCreating(false)} onCreated={(k) => { setCreating(false); setRevealed(k); keys.reload(); }} />}
      {revealed && (
        <Modal title="Your new key" onClose={() => setRevealed(null)} footer={<Button variant="primary" onClick={() => setRevealed(null)}>Done</Button>}>
          <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] warn">Shown once. Copy it now.</div>
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
      <p className="muted text-muted" style={{ fontSize: 12.5, marginTop: -6 }}>
        Requires a key for this environment in the <code>apikey</code> header — a 200 proves the key resolves and the
        environment is up.
      </p>
      <CopyText text={health} />
    </Card>
  );
}

const EXPIRY = [["", "Never"], ["30", "30 days"], ["90", "90 days"], ["365", "1 year"]];
const COLUMNS = "grid-cols-[minmax(0,1fr)_88px_88px]";

/** What a key may do, as a table: one row per area, a Read and a Write column. Management areas need a secret key. */
function Permissions({ catalog, role, value, onChange }) {
  const covered = (family, kind) => value.includes("*") || value.includes(`${family}:*`) || value.includes(`${family}:${kind}`);
  const toggle = (family, kind) => {
    const held = { read: covered(family, "read"), write: covered(family, "write") };
    held[kind] = !held[kind];
    const rest = value.includes("*") ? catalog.families.filter((f) => f.id !== family).map((f) => `${f.id}:*`) : value.filter((v) => !v.startsWith(`${family}:`));
    const own = held.read && held.write ? [`${family}:*`] : [held.read && `${family}:read`, held.write && `${family}:write`].filter(Boolean);
    onChange([...rest, ...own]);
  };
  const groups = [...new Set(catalog.families.map((f) => f.group))];
  const check = "h-4 w-4 cursor-pointer accent-[var(--brand)] disabled:cursor-not-allowed";
  return (
    <div className="overflow-hidden rounded-sm [border:1px_solid_var(--line)]">
      <div className={`grid ${COLUMNS} items-center gap-3 bg-sunken p-[8px_14px] text-2xs font-bold uppercase tracking-[.08em] text-muted`}>
        <span>Area</span><span className="text-center">Read</span><span className="text-center">Write</span>
      </div>
      {groups.map((group) => (
        <div key={group}>
          <div className="p-[10px_14px_4px] text-ui-sm font-semibold text-text [border-top:1px_solid_var(--line)]">{group}</div>
          {catalog.families.filter((f) => f.group === group).map((f) => {
            const locked = f.management && role !== "secret";
            return (
              <div key={f.id} className={`grid ${COLUMNS} items-center gap-3 p-[8px_14px] hover:bg-hover ${locked ? "opacity-45" : ""}`}>
                <div className="min-w-0">
                  <div className="text-ui font-medium">{f.label}</div>
                  <div className="text-ui-sm leading-[1.4] text-muted">{f.description}</div>
                </div>
                {["read", "write"].map((kind) => (
                  <span key={kind} className="grid place-items-center">
                    <input type="checkbox" className={check} disabled={locked} checked={!locked && covered(f.id, kind)} onChange={() => toggle(f.id, kind)} title={f[kind].label} aria-label={`${f.label}: ${f[kind].label}`} />
                  </span>
                ))}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function summary(value) {
  if (!value.length) return "Full access: no permission limits.";
  const n = value.reduce((total, v) => total + (v.endsWith(":*") && v !== "*" ? 2 : 1), 0);
  return `${n} permission${n === 1 ? "" : "s"} selected.`;
}

function NewKey({ base, catalog, onClose, onCreated }) {
  const [data, setData] = useState({ name: "", role: "publishable", scopes: [], allowed_ips: "", allowed_routes: "", days: "", preset: "full" });
  const [run, busy] = useAction();
  const split = (value) => value.split(",").map((s) => s.trim()).filter(Boolean);
  const management = new Set((catalog?.families || []).filter((f) => f.management).map((f) => f.id));
  const presets = (catalog?.presets || []).filter((p) => data.role === "secret" || !p.scopes.some((s) => management.has(s.split(":")[0])));
  const chosen = presets.find((p) => p.id === data.preset);
  const setRole = (role) => setData({ ...data, role, preset: "full", scopes: role === "secret" ? data.scopes : data.scopes.filter((s) => !management.has(s.split(":")[0])) });
  return (
    <Modal title="New API key" onClose={onClose} footer={<>
      <span className="mr-auto text-ui-sm text-muted">{summary(data.scopes)}</span>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" disabled={busy || !data.name} onClick={async () => {
        const expires_at = data.days ? new Date(Date.now() + Number(data.days) * 86400000).toISOString() : undefined;
        const r = await run(() => post(base, { name: data.name, role: data.role, scopes: data.scopes, expires_at, allowed_ips: split(data.allowed_ips), allowed_routes: split(data.allowed_routes) }));
        if (r) onCreated(r);
      }}>Create key</Button>
    </>}>
      <Section title="Key" icon="keys">
        <Field label="Name"><input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} placeholder="web app" /></Field>
        <Choices label="Key type" columns={2} value={data.role} onChange={setRole} options={[
          ["publishable", "Publishable", "For browsers and apps. Your policies apply."],
          ["secret", "Secret", "For servers. Bypasses policies and can manage the environment."],
        ]} />
      </Section>
      <Section title="Permissions" icon="policies" description="Anything outside these is refused at the gateway, whichever route it uses.">
        <Field label="Start from" hint={chosen?.description}>
          <select value={data.preset} onChange={(e) => { const p = presets.find((x) => x.id === e.target.value); setData({ ...data, preset: e.target.value, scopes: p ? p.scopes : data.scopes }); }}>
            {data.preset === "custom" && <option value="custom">Custom</option>}
            {presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </Field>
        {catalog ? <Permissions catalog={catalog} role={data.role} value={data.scopes} onChange={(scopes) => setData({ ...data, scopes, preset: "custom" })} /> : <p className="m-0 text-muted">Loading permissions…</p>}
        {data.role !== "secret" && <p className="m-0 text-ui-sm text-muted">Areas that manage the environment need a secret key.</p>}
      </Section>
      <Section title="Limits" icon="policies" description="Optional.">
        <Field label="Expires"><select value={data.days} onChange={(e) => setData({ ...data, days: e.target.value })}>{EXPIRY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        <Field label="Allowed IP ranges" hint="Comma-separated IPs or CIDRs. Every other address is refused."><input value={data.allowed_ips} onChange={(e) => setData({ ...data, allowed_ips: e.target.value })} placeholder="203.0.113.0/24" /></Field>
        <Field label="Allowed routes" hint="Comma-separated: GET /rest/v1/* or POST /flows/v1/*."><input value={data.allowed_routes} onChange={(e) => setData({ ...data, allowed_routes: e.target.value })} placeholder="GET /rest/v1/*" /></Field>
      </Section>
    </Modal>
  );
}
