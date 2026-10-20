import { Field, Loading, Segmented, Switch } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "../auth/shared";

const IDENTIFIERS = [["email", "Email"], ["username", "Username"], ["phone", "Phone number"]];
const DEFAULT_FORM = { sign_in_with: ["email"], attributes: {}, custom: [], locked: [], claims: [], steps: "auto" };

/** How people register and which identifiers they sign in with. */
export default function SignIn({ env }) {
  const config = useAuthConfig(env);
  const { value, set } = config;
  const form = { ...DEFAULT_FORM, ...(value.signup_form || {}) };
  const setForm = (values) => set({ signup_form: { ...form, ...values } });
  const identifiers = form.sign_in_with || ["email"];
  const toggle = (key) => {
    if (key === "email") return;
    const next = identifiers.includes(key) ? identifiers.filter((i) => i !== key) : [...identifiers, key];
    setForm({ sign_in_with: IDENTIFIERS.map(([k]) => k).filter((k) => k === "email" || next.includes(k)) });
  };
  return (
    <Loading state={config.state}>
      {() => (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
          <SettingsPanel icon="enter" title="Sign in with" description="Which identifiers a person can type on the sign-in page. Email is always collected, because verification and recovery go there." config={config}>
            <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
              {IDENTIFIERS.map(([key, label]) => (
                <label key={key} className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui">
                  <input type="checkbox" checked={identifiers.includes(key)} disabled={key === "email"} onChange={() => toggle(key)} />
                  <span>{label}{key === "email" ? " (always)" : ""}</span>
                </label>
              ))}
              <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>A username or phone number you allow for sign-in becomes required at sign-up and must be unique.</p>
            </div>
          </SettingsPanel>

          <SettingsPanel icon="users" title="Registration" config={config}>
            <Switch checked={value.signup_enabled} onChange={(v) => set({ signup_enabled: v })} label="Self-registration" hint="Anyone may create an account. Turn off to create people yourself or by invitation." />
            <Switch checked={value.require_email_verification} onChange={(v) => set({ require_email_verification: v })} label="Verify email before sign-in" hint="A new account must open the link in its confirmation email first." />
            <Switch checked={value.magic_link_enabled} onChange={(v) => set({ magic_link_enabled: v })} label="Sign-in links" hint="Offer “email me a link” on the sign-in page." />
            <Field label="Sign-up form layout" hint="Two steps puts the account on the first screen and the rest on the second. Auto does that when more than two details are asked.">
              <Segmented options={[["auto", "Auto"], ["single", "One page"], ["two", "Two steps"]]} value={form.steps} onChange={(v) => setForm({ steps: v })} />
            </Field>
          </SettingsPanel>
        </div>
      )}
    </Loading>
  );
}
