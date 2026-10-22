import { Badge, Button, Card, Field, Loading, Switch, Table, useAction, when } from "../../../components/ui";
import { del, useApi } from "../../../lib/api";
import { AUTH, SettingsPanel, useAuthConfig, useSection } from "../auth/shared";

/** Which devices people sign in from, and whether a known device may skip the second factor. */
export default function Devices({ env, base }) {
  const config = useAuthConfig(env);
  const [policy, setPolicy] = useSection(config, "devices");
  const devices = useApi(`${base}/devices`, { ...AUTH, interval: 30000 });
  const [run, busy] = useAction();
  const forget = async (row) => { if (await run(() => del(`${base}/users/${row.user_id}/devices/${row.id}`, AUTH), "Device forgotten")) devices.reload(); };
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <Loading state={config.state}>
        {() => (
          <SettingsPanel icon="terminal" title="Device tracking" description="Pawabase remembers the browsers and apps a person signs in from." config={config}>
            <Switch checked={policy.track} onChange={(v) => setPolicy({ track: v })} label="Track devices" hint="Recorded at sign-in, by the id the app or hosted page keeps." />
            <Switch checked={policy.notify_new} onChange={(v) => setPolicy({ notify_new: v })} label="Email when a new device signs in" hint="Not sent for a person's first ever sign-in." />
            <div className="grid grid gap-3.5 grid-cols-[repeat(auto-fill,_minmax(220px,_1fr))] [&.wide]:grid-cols-[repeat(auto-fill,_minmax(320px,_1fr))] [&.two]:grid-cols-2 [&.three]:grid-cols-3 max-wide:[&.three.insights]:grid-cols-2 max-navigation:[&.two]:grid-cols-1 max-navigation:[&.three]:grid-cols-1 max-navigation:[&.three.insights]:grid-cols-1 [&:has(>_.tile)]:grid [&:has(>_.tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.tile)]:gap-0 [&:has(>_.tile)]:[border:1px_solid_var(--line)] [&:has(>_.tile)]:rounded-sm [&:has(>_.tile)]:overflow-hidden [&:has(>_.tile)]:mb-4 [&:has(>_.stat-tile)]:grid [&:has(>_.stat-tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.stat-tile)]:gap-0 [&:has(>_.stat-tile)]:[border:1px_solid_var(--line)] [&:has(>_.stat-tile)]:rounded-sm [&:has(>_.stat-tile)]:overflow-hidden [&:has(>_.stat-tile)]:mb-4 two">
              <Field label="Remember a device after two-step verification" hint="Days a device may skip the second factor. 0 always asks. Only a device with its own id can be remembered.">
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><input type="number" min={0} max={365} value={policy.remember_days} onChange={(e) => setPolicy({ remember_days: Math.min(365, Math.max(0, Number(e.target.value) || 0)) })} style={{ maxWidth: 120 }} /><span className="muted text-muted">days</span></div>
              </Field>
              <Field label="Devices per person" hint="0 is unlimited. Over the limit, the least recently used is forgotten.">
                <input type="number" min={0} max={100} value={policy.max_per_user} onChange={(e) => setPolicy({ max_per_user: Math.min(100, Math.max(0, Number(e.target.value) || 0)) })} style={{ maxWidth: 120 }} />
              </Field>
            </div>
          </SettingsPanel>
        )}
      </Loading>
      <Card icon="terminal" flush title="Recent devices">
        <Loading state={devices} empty="No devices yet.">
          {() => (
            <Table
              rows={devices.data?.data || []}
              empty="No devices yet."
              columns={[
                { label: "Person", render: (d) => <b>{d.user_email}</b> },
                { label: "Device", render: (d) => d.label || "Unknown" },
                { label: "Last address", render: (d) => <code>{d.last_ip || "—"}</code> },
                { label: "Sign-ins", render: (d) => d.sign_ins },
                { label: "Trusted", render: (d) => (d.trusted ? <Badge tone="green">until {when(d.trusted_until)}</Badge> : <span className="faint text-faint">—</span>) },
                { label: "Last seen", render: (d) => when(d.last_seen_at) },
                { label: "", render: (d) => <Button size="sm" disabled={busy} onClick={() => forget(d)}>Forget</Button> },
              ]}
            />
          )}
        </Loading>
      </Card>
    </div>
  );
}
