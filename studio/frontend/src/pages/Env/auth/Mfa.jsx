import { useState } from "react";
import { Badge, Button, Card, Field, Loading, Segmented, Switch, Table, useAction, when } from "../../../components/ui";
import { post, useApi } from "../../../lib/api";
import { AUTH, Avatar, SettingsPanel, useAuthConfig, useSection } from "./shared";

const LABELS = { totp: "authenticator app", email: "email code" };

/** Which second-factor strategies exist, who must use one, and who has. */
export default function Mfa({ env, base }) {
  const config = useAuthConfig(env);
  const [policy, setPolicy] = useSection(config, "mfa");
  const [view, setView] = useState("mfa");
  const users = useApi(`${base}/users`, { ...AUTH, params: { status: view, limit: 100 } });
  const [run, busy] = useAction();
  const reset = async (user) => { if (confirm(`Remove ${user.email}'s second factors? They will sign in with a password alone until they enrol again.`) && await run(() => post(`${base}/users/${user.id}/mfa/reset`, {}, AUTH), "Second factors removed")) users.reload(); };
  const off = !config.value.mfa_enabled;
  return (
    <div className="stack lg">
      <Loading state={config.state}>
        {() => (
          <>
            <SettingsPanel title="Two-step verification" description="A second step after the password. A completed challenge makes the session aal2, which policies can require." config={config}>
              <Switch checked={config.value.mfa_enabled} onChange={(v) => config.set({ mfa_enabled: v })} label="Allow two-step verification" hint="Off hides enrolment and stops asking. People already enrolled keep their factors." />
            </SettingsPanel>

            <SettingsPanel title="Strategies" description="What people can use as a second step. They may enrol in more than one." config={config}>
              <Switch checked={policy.totp} disabled={off} onChange={(v) => setPolicy({ totp: v })} label="Authenticator app" hint="A six-digit code from Google Authenticator, 1Password, Authy and the like." />
              <Switch checked={policy.email} onChange={(v) => setPolicy({ email: v })} label="Email code" hint="A six-digit code sent to the account's address. Convenient, and only as safe as the mailbox." />
              <Switch checked={policy.backup_codes} onChange={(v) => setPolicy({ backup_codes: v })} label="Backup codes" hint="One-time codes shown once at enrolment, for when the phone is lost." />
              {policy.backup_codes && (
                <Field label="Backup codes per person"><input type="number" min={4} max={20} value={policy.backup_code_count} onChange={(e) => setPolicy({ backup_code_count: Math.min(20, Math.max(4, Number(e.target.value) || 10)) })} style={{ maxWidth: 120 }} /></Field>
              )}
              {policy.email && (
                <Field label="Emailed code lifetime" hint="Minutes."><input type="number" min={5} max={60} value={policy.email_code_minutes} onChange={(e) => setPolicy({ email_code_minutes: Math.min(60, Math.max(5, Number(e.target.value) || 10)) })} style={{ maxWidth: 120 }} /></Field>
              )}
            </SettingsPanel>

            <SettingsPanel title="Who must use it" description="When required, a person without a second factor is walked through setting one up before they get a session." config={config}>
              <Field label="Require two-step verification">
                <Segmented options={[["off", "Nobody"], ["admins", "Administrators"], ["all", "Everyone"]]} value={policy.require} onChange={(v) => setPolicy({ require: v })} />
              </Field>
              {policy.require !== "off" && (
                <Field label="Grace period" hint="Days a new account may sign in without one. 0 asks at the first sign-in.">
                  <div className="row"><input type="number" min={0} max={90} value={policy.grace_days} onChange={(e) => setPolicy({ grace_days: Math.min(90, Math.max(0, Number(e.target.value) || 0)) })} style={{ maxWidth: 120 }} /><span className="muted">days</span></div>
                </Field>
              )}
              {policy.require === "admins" && <p className="hint" style={{ margin: 0 }}>Administrators are people with an admin or owner role, staff accounts, and owners or admins of an organization.</p>}
              {policy.require !== "off" && !policy.totp && !policy.email && <p className="note bad" style={{ margin: 0 }}>Turn on at least one strategy, or nobody can enrol and the requirement is ignored.</p>}
            </SettingsPanel>
          </>
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
                { label: "Second factors", render: (u) => ((u.mfa_factors || []).length ? <span className="row" style={{ gap: 6 }}>{u.mfa_factors.map((k) => <Badge key={k} tone="blue">{LABELS[k] || k}</Badge>)}</span> : u.mfa_enabled ? <Badge tone="blue">enrolled</Badge> : <span className="faint">none</span>) },
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
