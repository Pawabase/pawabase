import { Badge, Button, Card, Field, Loading, Switch, Table, useAction, when } from "../../../components/ui";
import { del, useApi } from "../../../lib/api";
import { AUTH, SettingsPanel, useAuthConfig, useSection } from "../auth/shared";

/** Keys people create for themselves to call the API as themselves. */
export default function ApiKeys({ env, base }) {
  const config = useAuthConfig(env);
  const [policy, setPolicy] = useSection(config, "api_keys");
  const keys = useApi(`${base}/api-keys`, { ...AUTH, interval: 30000 });
  const [run, busy] = useAction();
  const revoke = async (row) => { if (await run(() => del(`${base}/api-keys/${row.id}`, AUTH), "Key revoked")) keys.reload(); };
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <Loading state={config.state}>
        {() => (
          <SettingsPanel icon="keys" title="User API keys" description="A person creates a key, then sends it as Authorization: Bearer pbu_… along with your project key. It acts as them, with their roles and permissions, narrowed to the scopes they choose. Keys stop working when their owner is disabled." config={config}>
            <Switch checked={policy.enabled} onChange={(v) => setPolicy({ enabled: v })} label="Let people create API keys" hint="Off, existing keys stop working too." />
            <div className="grid grid gap-3.5 grid-cols-[repeat(auto-fill,_minmax(220px,_1fr))] [&.wide]:grid-cols-[repeat(auto-fill,_minmax(320px,_1fr))] [&.two]:grid-cols-2 [&.three]:grid-cols-3 max-wide:[&.three.insights]:grid-cols-2 max-navigation:[&.two]:grid-cols-1 max-navigation:[&.three]:grid-cols-1 max-navigation:[&.three.insights]:grid-cols-1 [&:has(>_.tile)]:grid [&:has(>_.tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.tile)]:gap-0 [&:has(>_.tile)]:[border:1px_solid_var(--line)] [&:has(>_.tile)]:rounded-sm [&:has(>_.tile)]:overflow-hidden [&:has(>_.tile)]:mb-4 [&:has(>_.stat-tile)]:grid [&:has(>_.stat-tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.stat-tile)]:gap-0 [&:has(>_.stat-tile)]:[border:1px_solid_var(--line)] [&:has(>_.stat-tile)]:rounded-sm [&:has(>_.stat-tile)]:overflow-hidden [&:has(>_.stat-tile)]:mb-4 two">
              <Field label="Keys per person"><input type="number" min={1} max={100} value={policy.max_per_user} onChange={(e) => setPolicy({ max_per_user: Math.min(100, Math.max(1, Number(e.target.value) || 10)) })} style={{ maxWidth: 140 }} /></Field>
              <Field label="Longest a key may live" hint="Days. 0 lets a key never expire."><div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><input type="number" min={0} max={3650} value={policy.max_days} onChange={(e) => setPolicy({ max_days: Math.min(3650, Math.max(0, Number(e.target.value) || 0)) })} style={{ maxWidth: 140 }} /><span className="muted text-muted">days</span></div></Field>
            </div>
            <Switch checked={policy.scoped} onChange={(v) => setPolicy({ scoped: v })} label="Allow scoped keys" hint="A key may be limited to some of its owner's permissions." />
          </SettingsPanel>
        )}
      </Loading>
      <Card icon="keys" flush title="Keys in this environment">
        <Loading state={keys} empty="Nobody has created a key.">
          {() => (
            <Table
              rows={keys.data?.data || []}
              empty="Nobody has created a key."
              columns={[
                { label: "Name", render: (k) => <b>{k.name}</b> },
                { label: "Owner", render: (k) => k.user_email },
                { label: "Key", render: (k) => <code>{k.prefix}…</code> },
                { label: "Scopes", render: (k) => (k.scopes.length ? k.scopes.join(", ") : <span className="faint text-faint">all of the owner's</span>) },
                { label: "Status", render: (k) => <Badge tone={k.status === "active" ? "green" : "yellow"}>{k.status}</Badge> },
                { label: "Last used", render: (k) => (k.last_used_at ? when(k.last_used_at) : <span className="faint text-faint">never</span>) },
                { label: "Expires", render: (k) => (k.expires_at ? when(k.expires_at) : <span className="faint text-faint">never</span>) },
                { label: "", render: (k) => (k.status === "active" ? <Button size="sm" disabled={busy} onClick={() => revoke(k)}>Revoke</Button> : null) },
              ]}
            />
          )}
        </Loading>
      </Card>
    </div>
  );
}
