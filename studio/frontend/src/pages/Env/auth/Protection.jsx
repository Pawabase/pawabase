import { Badge, Button, Card, Field, Loading, Table, useAction, when } from "../../../components/ui";
import { patch, useApi } from "../../../lib/api";
import { AUTH, Avatar, SettingsPanel, useAuthConfig } from "./shared";

/** Locking accounts after repeated failures, and who is locked now. */
export default function Protection({ env, base }) {
  const config = useAuthConfig(env);
  const locked = useApi(`${base}/users`, { ...AUTH, params: { status: "locked" }, interval: 15000 });
  const failures = useApi(`${base}/events`, { ...AUTH, params: { failed: "true" }, interval: 15000 });
  const [run, busy] = useAction();
  const unlock = async (user) => { if (await run(() => patch(`${base}/users/${user.id}`, { unlock: true }, AUTH), "Unlocked")) locked.reload(); };
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <Loading state={config.state}>
        {() => (
          <SettingsPanel title="Lockout" description="After this many wrong passwords or codes in a row, the account locks for a while. A correct sign-in clears the count." config={config}>
            <div className="grid grid gap-3.5 grid-cols-[repeat(auto-fill,_minmax(220px,_1fr))] [&.wide]:grid-cols-[repeat(auto-fill,_minmax(320px,_1fr))] [&.two]:grid-cols-2 [&.three]:grid-cols-3 max-wide:[&.three.insights]:grid-cols-2 max-navigation:[&.two]:grid-cols-1 max-navigation:[&.three]:grid-cols-1 max-navigation:[&.three.insights]:grid-cols-1 [&:has(>_.tile)]:grid [&:has(>_.tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.tile)]:gap-0 [&:has(>_.tile)]:[border:1px_solid_var(--line)] [&:has(>_.tile)]:rounded-sm [&:has(>_.tile)]:overflow-hidden [&:has(>_.tile)]:mb-4 [&:has(>_.stat-tile)]:grid [&:has(>_.stat-tile)]:grid-cols-[repeat(auto-fit,_minmax(160px,_1fr))] [&:has(>_.stat-tile)]:gap-0 [&:has(>_.stat-tile)]:[border:1px_solid_var(--line)] [&:has(>_.stat-tile)]:rounded-sm [&:has(>_.stat-tile)]:overflow-hidden [&:has(>_.stat-tile)]:mb-4 two">
              <Field label="Failed attempts before a lock" hint="Leave empty for the installation default."><input type="number" min={2} max={100} value={config.value.lockout_threshold ?? ""} onChange={(e) => config.set({ lockout_threshold: e.target.value ? Number(e.target.value) : null })} placeholder="Default" /></Field>
              <Field label="Lock for (minutes)" hint="Leave empty for the installation default."><input type="number" min={1} max={1440} value={config.value.lockout_minutes ?? ""} onChange={(e) => config.set({ lockout_minutes: e.target.value ? Number(e.target.value) : null })} placeholder="Default" /></Field>
            </div>
          </SettingsPanel>
        )}
      </Loading>
      <Card flush title="Locked accounts">
        <Loading state={locked} empty="No account is locked.">
          {(data) => (
            <Table
              rows={data.data}
              empty="No account is locked."
              columns={[
                { label: "User", render: (u) => <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><Avatar text={u.email} size={28} /><b>{u.email}</b></span> },
                { label: "Last sign-in IP", render: (u) => <code>{u.last_sign_in_ip || "—"}</code> },
                { label: "Locked until", render: (u) => when(u.locked_until) },
                { label: "", render: (u) => <Button size="sm" disabled={busy} onClick={() => unlock(u)}>Unlock</Button> },
              ]}
            />
          )}
        </Loading>
      </Card>
      <Card flush title="Recent failed sign-ins">
        <Loading state={failures} empty="No failed attempts.">
          {(data) => (
            <Table
              rows={(data.data || []).slice(0, 20)}
              empty="No failed attempts."
              columns={[
                { label: "When", render: (e) => when(e.created_at) },
                { label: "Email", render: (e) => e.email || <span className="faint text-faint">—</span> },
                { label: "Reason", render: (e) => <Badge tone="red">{e.reason || "failed"}</Badge> },
                { label: "IP", render: (e) => <code>{e.ip || "—"}</code> },
              ]}
            />
          )}
        </Loading>
      </Card>
    </div>
  );
}
