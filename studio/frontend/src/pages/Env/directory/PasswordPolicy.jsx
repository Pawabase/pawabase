import { Field, Loading, Segmented, Switch } from "../../../components/ui";
import { SettingsPanel, useAuthConfig, useSection } from "../auth/shared";

const CLASSES = [["upper", "Uppercase letter"], ["lower", "Lowercase letter"], ["digit", "Number"], ["symbol", "Symbol"]];

function Number_({ label, hint, value, onChange, min = 0, max = 1000, unit }) {
  return (
    <Field label={label} hint={hint}>
      <div className="row"><input type="number" min={min} max={max} value={value} onChange={(e) => onChange(Math.min(max, Math.max(min, Number(e.target.value) || 0)))} style={{ maxWidth: 140 }} />{unit && <span className="muted">{unit}</span>}</div>
    </Field>
  );
}

/** Everything about what a password must be and how long it lives. */
export default function PasswordPolicy({ env }) {
  const config = useAuthConfig(env);
  const { value, set } = config;
  const [policy, setPolicy] = useSection(config, "passwords");
  const rules = value.password_rules || {};
  return (
    <Loading state={config.state}>
      {() => (
        <div className="stack lg">
          <SettingsPanel title="Strength" description="Applied when someone signs up, resets a password, changes it, or an operator sets one. Existing passwords keep working until they are changed." config={config}>
            <Field label="Policy"><Segmented options={[["basic", "Length and character rules"], ["strict", "Also refuse common passwords"]]} value={value.password_policy} onChange={(v) => set({ password_policy: v })} /></Field>
            <div className="grid two">
              <Number_ label="Minimum length" hint="Characters." value={value.password_min_length} min={6} max={128} onChange={(v) => set({ password_min_length: v || 8 })} />
              <Number_ label="Maximum length" hint="Longer passwords are refused." value={policy.max_length} min={16} max={1024} onChange={(v) => setPolicy({ max_length: v || 128 })} />
            </div>
            <Field label="Must contain">
              <div className="row" style={{ flexWrap: "wrap", gap: 18 }}>
                {CLASSES.map(([key, label]) => <label key={key} className="check"><input type="checkbox" checked={!!rules[key]} onChange={(e) => set({ password_rules: { ...rules, [key]: e.target.checked } })} /> {label}</label>)}
              </div>
            </Field>
            <Number_ label="Different characters" hint="At least this many distinct characters, so “aaaaaaaa1” does not pass. 0 turns it off." value={policy.min_unique} max={32} onChange={(v) => setPolicy({ min_unique: v })} />
          </SettingsPanel>

          <SettingsPanel title="Reuse and expiry" config={config}>
            <Number_ label="Remember previous passwords" hint="A person cannot go back to one of their last N passwords. 0 turns it off." value={policy.history} max={24} onChange={(v) => setPolicy({ history: v })} />
            <Number_ label="Passwords expire after" hint="After this many days a person must choose a new password through “Forgot password” before signing in. 0 means never." value={policy.max_age_days} max={3650} unit="days" onChange={(v) => setPolicy({ max_age_days: v })} />
          </SettingsPanel>

          <SettingsPanel title="Guessable passwords" config={config}>
            <Switch checked={policy.forbid_personal} onChange={(v) => setPolicy({ forbid_personal: v })} label="Refuse passwords containing the person's name, username or email" />
            <Switch checked={policy.breach_check} onChange={(v) => setPolicy({ breach_check: v })} label="Refuse passwords found in known data breaches" hint="Checked with haveibeenpwned's range service: only the first five characters of a hash leave your server. If the service is unreachable the password is accepted." />
          </SettingsPanel>
        </div>
      )}
    </Loading>
  );
}
