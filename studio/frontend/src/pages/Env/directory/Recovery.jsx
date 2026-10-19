import { Field, Loading, Switch } from "../../../components/ui";
import { SettingsPanel, useAuthConfig, useSection } from "../auth/shared";

/** How a person gets back into an account they cannot sign in to. */
export default function Recovery({ env }) {
  const config = useAuthConfig(env);
  const [policy, setPolicy] = useSection(config, "recovery");
  return (
    <Loading state={config.state}>
      {() => (
        <SettingsPanel title="Account recovery" description="What happens when someone forgets their password." config={config}>
          <Switch checked={policy.enabled} onChange={(v) => setPolicy({ enabled: v })} label="Self-service password reset" hint="Off, nobody can reset their own password: an administrator sets it." />
          <Field label="Reset link lifetime" hint="Minutes. A link works once.">
            <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><input type="number" min={5} max={1440} value={policy.link_minutes} onChange={(e) => setPolicy({ link_minutes: Math.min(1440, Math.max(5, Number(e.target.value) || 60)) })} style={{ maxWidth: 140 }} /><span className="muted text-muted">minutes</span></div>
          </Field>
          <Switch checked={policy.revoke_sessions} onChange={(v) => setPolicy({ revoke_sessions: v })} label="Sign out everywhere after a reset" hint="Anyone who had the old password loses their session." />
          <Switch checked={policy.notify_change} onChange={(v) => setPolicy({ notify_change: v })} label="Email the person when their password is reset" hint="So they notice if it was not them." />
        </SettingsPanel>
      )}
    </Loading>
  );
}
