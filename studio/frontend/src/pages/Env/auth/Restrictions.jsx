import { useState } from "react";
import { Badge, Field, Loading, Switch, TagInput } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "./shared";

const clean = (list) => (list || []).map((d) => d.trim().toLowerCase().replace(/^@/, "")).filter(Boolean);

/** Mirrors the server's decision, so an operator can check a rule before saving it. */
function verdict(email, allowed, blocked) {
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return null;
  const domain = email.split("@").pop().toLowerCase();
  if (clean(blocked).includes(domain)) return { ok: false, why: `${domain} is blocked` };
  if (clean(allowed).length && !clean(allowed).includes(domain)) return { ok: false, why: `${domain} is not on the allow list` };
  return { ok: true, why: clean(allowed).length ? `${domain} is on the allow list` : "No restriction applies" };
}

/** Who may create an account. Operators can always create users by hand. */
export default function Restrictions({ env }) {
  const config = useAuthConfig(env);
  const { value, set } = config;
  const [probe, setProbe] = useState("");
  const result = verdict(probe, value.allowed_email_domains, value.blocked_email_domains);
  return (
    <Loading state={config.state}>
      {() => (
        <div className="stack lg">
          <SettingsPanel title="Sign-up" description="Whether strangers can create an account, and what they get when they do." config={config}>
            <Switch checked={value.signup_enabled} onChange={(v) => set({ signup_enabled: v })} label="Allow sign-ups" hint="Off makes the environment invitation-only: only operators create accounts." />
            <Switch checked={value.require_email_verification} onChange={(v) => set({ require_email_verification: v })} label="Require a verified email" hint="Unverified people cannot sign in until they confirm their address." />
            <Field label="Roles for new accounts" hint="Granted automatically at sign-up."><TagInput value={value.default_roles || []} onChange={(v) => set({ default_roles: v })} placeholder="customer" /></Field>
          </SettingsPanel>
          <SettingsPanel title="Email domains" description="Limit sign-ups to the companies you work with, or turn away throwaway mailboxes. Applies to every way of signing up: password, magic link and social sign-in." config={config}>
            <Field label="Only allow these domains" hint="Leave empty to allow any domain."><TagInput value={value.allowed_email_domains || []} onChange={(v) => set({ allowed_email_domains: clean(v) })} placeholder="acme.com" /></Field>
            <Field label="Always block these domains" hint="A blocked domain is refused even when the allow list is empty."><TagInput value={value.blocked_email_domains || []} onChange={(v) => set({ blocked_email_domains: clean(v) })} placeholder="mailinator.com" /></Field>
            <Field label="Try an address" hint="Checks the rules above, including changes you have not saved yet.">
              <div className="row">
                <input value={probe} onChange={(e) => setProbe(e.target.value)} placeholder="someone@example.com" />
                {result && <Badge tone={result.ok ? "green" : "red"}>{result.ok ? "allowed" : "refused"}</Badge>}
              </div>
              {result && <span className="hint">{result.why}.</span>}
            </Field>
          </SettingsPanel>
        </div>
      )}
    </Loading>
  );
}
