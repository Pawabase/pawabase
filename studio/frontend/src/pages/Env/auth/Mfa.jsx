import { useState } from "react";
import { Badge, Button, Card, Loading, Segmented, Switch, Table, useAction, when } from "../../../components/ui";
import { post, useApi } from "../../../lib/api";
import { AUTH, Avatar, SettingsPanel, useAuthConfig } from "./shared";

/** Whether people may add an authenticator app, and who has. */
export default function Mfa({ env, base }) {
  const config = useAuthConfig(env);
  const [view, setView] = useState("mfa");
  const users = useApi(`${base}/users`, { ...AUTH, params: { status: view, limit: 100 } });
  const [run, busy] = useAction();
  const reset = async (user) => { if (confirm(`Remove ${user.email}'s second factors? They will sign in with a password alone until they enrol again.`) && await run(() => post(`${base}/users/${user.id}/mfa/reset`, {}, AUTH), "Second factors removed")) users.reload(); };
  return (
    <div className="stack lg">
      <Loading state={config.state}>
        {() => (
          <SettingsPanel title="Authenticator apps" description="TOTP codes from an app on the person's phone, with recovery codes for when the phone is gone. A completed challenge makes the session aal2, which policies can require." config={config}>
            <Switch checked={config.value.mfa_enabled} onChange={(v) => config.set({ mfa_enabled: v })} label="Let people enrol" hint="Off hides enrolment. People already enrolled keep their factor." />
          </SettingsPanel>
        )}
      </Loading>
      <Card flush title="People" actions={<Segmented options={[["mfa", "Enrolled"], ["no_mfa", "Not enrolled"]]} value={view} onChange={setView} />}>
        <Loading state={users} empty="Nobody here.">
          {(data) => (
            <Table
              rows={data.data}
              empty="Nobody here."
              columns={[
                { label: "User", render: (u) => <span className="row"><Avatar text={u.email} size={28} /><b>{u.email}</b></span> },
                { label: "Second factor", render: (u) => (u.mfa_enabled ? <Badge tone="blue">authenticator app</Badge> : <span className="faint">none</span>) },
                { label: "Last sign-in", render: (u) => when(u.last_sign_in_at) },
                { label: "", render: (u) => u.mfa_enabled && <Button size="sm" disabled={busy} onClick={() => reset(u)}>Reset</Button> },
              ]}
            />
          )}
        </Loading>
      </Card>
    </div>
  );
}
