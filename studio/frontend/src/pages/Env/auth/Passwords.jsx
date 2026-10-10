import { Field, Loading, Segmented, Switch } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "./shared";

/** How strong a password must be, and whether people can skip passwords altogether. */
export default function Passwords({ env }) {
  const config = useAuthConfig(env);
  const { value, set } = config;
  const strict = value.password_policy === "strict";
  return (
    <Loading state={config.state}>
      {() => (
        <div className="stack lg">
          <SettingsPanel title="Password strength" description="Applied when someone signs up, resets a password, or an operator sets one. Existing passwords keep working until they are changed." config={config}>
            <Field label="Policy">
              <Segmented options={[["basic", "Basic"], ["strict", "Strict"]]} value={value.password_policy} onChange={(v) => set({ password_policy: v })} />
            </Field>
            <Field label="Minimum length" hint="Characters. Passwords over 1,024 characters are always refused.">
              <input type="number" min={6} max={128} value={value.password_min_length} onChange={(e) => set({ password_min_length: Math.min(128, Math.max(6, Number(e.target.value) || 8)) })} style={{ maxWidth: 160 }} />
            </Field>
            <div className="rule-list">
              <span className="eyebrow">What people will be told</span>
              <ul>
                <li>At least {value.password_min_length} characters.</li>
                {strict && <li>Sillo's full password validator: it rejects common and trivially guessable passwords.</li>}
              </ul>
            </div>
          </SettingsPanel>
          <SettingsPanel title="Without a password" description="People who prefer not to remember one can sign in with a link sent to their email." config={config}>
            <Switch checked={value.magic_link_enabled} onChange={(v) => set({ magic_link_enabled: v })} label="Magic links" hint="A one-time link by email. Links expire and work once." />
          </SettingsPanel>
        </div>
      )}
    </Loading>
  );
}
