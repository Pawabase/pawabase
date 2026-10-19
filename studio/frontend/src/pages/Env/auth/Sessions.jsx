import { Badge, Button, Card, Field, Loading, Table, when } from "../../../components/ui";
import { del, post, useApi } from "../../../lib/api";
import { useAction } from "../../../components/ui";
import { AUTH, Avatar, SettingsPanel, device, duration, useAuthConfig } from "./shared";

/** Every active session in the environment, who holds it, and how long sessions last. */
export default function Sessions({ env, base }) {
  const sessions = useApi(`${base}/sessions`, { ...AUTH, interval: 20000 });
  const config = useAuthConfig(env);
  const [run, busy] = useAction();
  const rows = sessions.data?.data || [];
  const end = async (row) => { if (await run(() => del(`${base}/sessions/${row.id}`, AUTH), "Session ended")) sessions.reload(); };
  return (
    <div className="stack lg">
      <Card flush title="Active sessions" actions={<span className="muted small">{rows.length} signed in</span>}>
        <Loading state={sessions} empty="Nobody is signed in.">
          {() => (
            <Table
              rows={rows}
              empty="Nobody is signed in."
              columns={[
                { label: "User", render: (s) => <span className="row"><Avatar text={s.email} size={28} /><b>{s.email}</b></span> },
                { label: "Device", render: (s) => device(s.user_agent) },
                { label: "IP", render: (s) => <code>{s.ip || "—"}</code> },
                { label: "Method", render: (s) => <span className="row" style={{ gap: 6 }}><Badge>{s.method}</Badge>{s.aal === "aal2" && <Badge tone="blue">MFA</Badge>}</span> },
                { label: "Organization", render: (s) => s.org || <span className="faint">—</span> },
                { label: "Started", render: (s) => when(s.created_at) },
                { label: "Last active", render: (s) => when(s.last_refreshed_at || s.created_at) },
                { label: "", render: (s) => <Button size="sm" disabled={busy} onClick={() => end(s)}>End</Button> },
              ]}
            />
          )}
        </Loading>
      </Card>
      <Loading state={config.state}>
        {() => (
          <SettingsPanel title="Session lifetime" description="Access tokens are short and refresh quietly. A refresh token that is used twice ends the whole session, on the assumption it was stolen." config={config}>
            <div className="grid two">
              <Field label="Access token lifetime" hint={`Seconds. Now ${duration(config.value.access_ttl)}.`}><input type="number" min={60} value={config.value.access_ttl} onChange={(e) => config.set({ access_ttl: Number(e.target.value) || 900 })} /></Field>
              <Field label="Refresh token lifetime" hint={`Seconds. Now ${duration(config.value.refresh_ttl)}. This is how long someone stays signed in without activity.`}><input type="number" min={3600} value={config.value.refresh_ttl} onChange={(e) => config.set({ refresh_ttl: Number(e.target.value) || 2592000 })} /></Field>
            </div>
            <Field label="Sessions per user" hint="0 is unlimited. Over the limit, the oldest session ends when a new one starts.">
              <input type="number" min={0} max={100} value={config.value.max_sessions || 0} onChange={(e) => config.set({ max_sessions: Math.max(0, Number(e.target.value) || 0) })} style={{ maxWidth: 160 }} />
            </Field>
            <p className="hint" style={{ margin: 0 }}>Changes reach sign-ins within about 10 seconds, and apply to sessions that start after that.</p>
          </SettingsPanel>
        )}
      </Loading>
    </div>
  );
}
