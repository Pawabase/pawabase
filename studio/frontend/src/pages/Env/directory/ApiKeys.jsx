import { useState } from "react";
import { Badge, Button, Card, Field, Loading, Switch, Table, useAction, when } from "../../../components/ui";
import { del, useApi } from "../../../lib/api";
import { AUTH, SettingsButton, SettingsSheet, useAuthConfig, useSection } from "../auth/shared";

/** Keys people create for themselves to call the API as themselves. */
export default function ApiKeys({ env, base }) {
  const config = useAuthConfig(env);
  const [policy, setPolicy] = useSection(config, "api_keys");
  const keys = useApi(`${base}/api-keys`, { ...AUTH, interval: 30000 });
  const [run, busy] = useAction();
  const [settings, setSettings] = useState(false);
  const revoke = async (row) => { if (await run(() => del(`${base}/api-keys/${row.id}`, AUTH), "Key revoked")) keys.reload(); };
  return (
    <div className="stack lg">
      <Card flush title="Keys in this environment" actions={config.state.data && <SettingsButton label="User API keys" dirty={config.dirty} onClick={() => setSettings(true)} />}>
        <Loading state={keys} empty="Nobody has created a key.">
          {() => (
            <Table
              rows={keys.data?.data || []}
              empty="Nobody has created a key."
              columns={[
                { label: "Name", render: (k) => <b>{k.name}</b> },
                { label: "Owner", render: (k) => k.user_email },
                { label: "Key", render: (k) => <code>{k.prefix}…</code> },
                { label: "Scopes", render: (k) => (k.scopes.length ? k.scopes.join(", ") : <span className="faint">all of the owner's</span>) },
                { label: "Status", render: (k) => <Badge tone={k.status === "active" ? "green" : "yellow"}>{k.status}</Badge> },
                { label: "Last used", render: (k) => (k.last_used_at ? when(k.last_used_at) : <span className="faint">never</span>) },
                { label: "Expires", render: (k) => (k.expires_at ? when(k.expires_at) : <span className="faint">never</span>) },
                { label: "", render: (k) => (k.status === "active" ? <Button size="sm" disabled={busy} onClick={() => revoke(k)}>Revoke</Button> : null) },
              ]}
            />
          )}
        </Loading>
      </Card>
      {settings && (
        <SettingsSheet title="User API keys" icon="keys" description="A person creates a key, then sends it as Authorization: Bearer pbu_… along with your project key. It acts as them, with their roles and permissions, narrowed to the scopes they choose. Keys stop working when their owner is disabled." config={config} onClose={() => setSettings(false)}>
          <Switch checked={policy.enabled} onChange={(v) => setPolicy({ enabled: v })} label="Let people create API keys" hint="Off, existing keys stop working too." />
          <div className="grid two">
            <Field label="Keys per person"><input type="number" min={1} max={100} value={policy.max_per_user} onChange={(e) => setPolicy({ max_per_user: Math.min(100, Math.max(1, Number(e.target.value) || 10)) })} style={{ maxWidth: 140 }} /></Field>
            <Field label="Longest a key may live" hint="Days. 0 lets a key never expire."><div className="row"><input type="number" min={0} max={3650} value={policy.max_days} onChange={(e) => setPolicy({ max_days: Math.min(3650, Math.max(0, Number(e.target.value) || 0)) })} style={{ maxWidth: 140 }} /><span className="muted">days</span></div></Field>
          </div>
          <Switch checked={policy.scoped} onChange={(v) => setPolicy({ scoped: v })} label="Allow scoped keys" hint="A key may be limited to some of its owner's permissions." />
        </SettingsSheet>
      )}
    </div>
  );
}
