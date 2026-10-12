import { usePage } from "@inertiajs/react";
import { useMemo, useState } from "react";
import { Card, Field, Loading, Segmented, TagInput } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "./shared";

const DEFAULTS = {
  name: "", logo_url: "", primary: "#6d5bd0", background: "#f3f1fb", card: "#ffffff", text: "#1f1b2e",
  radius: 14, font: "system", theme: "light", login_title: "Welcome back", login_subtitle: "",
  signup_title: "Create your account", signup_subtitle: "", terms_url: "", privacy_url: "", footer: "",
};
const FONTS = [["system", "System"], ["rounded", "Rounded"], ["serif", "Serif"], ["mono", "Mono"]];
const PAGE_OPTIONS = [["login", "Sign in"], ["signup", "Sign up"], ["forgot", "Forgot password"]];

function encode(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  let binary = "";
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function Color({ label, value, onChange }) {
  return (
    <Field label={label}>
      <div className="row">
        <input type="color" value={value} onChange={(e) => onChange(e.target.value)} style={{ width: 46, height: 40, padding: 3 }} aria-label={label} />
        <input value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} style={{ fontFamily: "var(--mono)" }} />
      </div>
    </Field>
  );
}

/** How the hosted sign-in pages look, with a live preview of exactly what a person will see. */
export default function Pages({ env }) {
  const config = useAuthConfig(env);
  const { gateway_url: gatewayUrl } = usePage().props;
  const [page, setPage] = useState("login");
  const branding = { ...DEFAULTS, ...(config.value.branding || {}) };
  const set = (values) => config.set({ branding: { ...(config.value.branding || {}), ...values } });
  const base = (gatewayUrl || "").replace(/\/$/, "");
  const previewUrl = useMemo(
    () => `${base}/auth/v1/hosted/${env}/preview/${page}?b=${encode(config.value.branding || {})}`,
    [base, env, page, config.value.branding],
  );
  const signInUrl = `${base}/auth/v1/hosted/${env}/login?redirect_to=${encodeURIComponent(config.value.site_url || "https://yourapp.example.com/callback")}`;

  return (
    <Loading state={config.state}>
      {() => (
        <div className="pages-grid">
          <div className="stack lg">
            <SettingsPanel title="Look" description="Pawabase hosts the sign-in, sign-up and password-reset pages. Make them look like your product." config={config}>
              <Field label="Product name" hint="Shown above the form when there is no logo."><input value={branding.name} onChange={(e) => set({ name: e.target.value })} placeholder="Acme" maxLength={80} /></Field>
              <Field label="Logo address" hint="An https image, up to 44px tall."><input value={branding.logo_url} onChange={(e) => set({ logo_url: e.target.value })} placeholder="https://acme.com/logo.svg" /></Field>
              <div className="grid2">
                <Color label="Brand colour" value={branding.primary} onChange={(v) => set({ primary: v })} />
                <Color label="Page background" value={branding.background} onChange={(v) => set({ background: v })} />
                <Color label="Card" value={branding.card} onChange={(v) => set({ card: v })} />
                <Color label="Text" value={branding.text} onChange={(v) => set({ text: v })} />
              </div>
              <div className="grid2">
                <Field label={`Corner radius: ${branding.radius}px`}><input type="range" min="0" max="28" value={branding.radius} onChange={(e) => set({ radius: Number(e.target.value) })} /></Field>
                <Field label="Font"><select value={branding.font} onChange={(e) => set({ font: e.target.value })}>{FONTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
              </div>
              <Field label="Theme"><Segmented options={[["light", "Light"], ["dark", "Dark"], ["auto", "Match the device"]]} value={branding.theme} onChange={(v) => set({ theme: v })} /></Field>
            </SettingsPanel>

            <Card title="Words">
              <div className="stack lg">
                <Field label="Sign-in heading"><input value={branding.login_title} onChange={(e) => set({ login_title: e.target.value })} maxLength={80} /></Field>
                <Field label="Sign-in subheading" optional><input value={branding.login_subtitle} onChange={(e) => set({ login_subtitle: e.target.value })} maxLength={200} /></Field>
                <Field label="Sign-up heading"><input value={branding.signup_title} onChange={(e) => set({ signup_title: e.target.value })} maxLength={80} /></Field>
                <Field label="Terms of service address" optional><input value={branding.terms_url} onChange={(e) => set({ terms_url: e.target.value })} placeholder="https://acme.com/terms" /></Field>
                <Field label="Privacy policy address" optional><input value={branding.privacy_url} onChange={(e) => set({ privacy_url: e.target.value })} placeholder="https://acme.com/privacy" /></Field>
                <Field label="Footer line" optional><input value={branding.footer} onChange={(e) => set({ footer: e.target.value })} maxLength={200} placeholder="© Acme Inc." /></Field>
              </div>
            </Card>

            <Card title="Use it from your app">
              <div className="stack">
                <p className="muted" style={{ margin: 0 }}>Send people here, then exchange the code your app receives. Only addresses under Domains may receive it.</p>
                <pre className="code-block">{`# 1. send the person to the hosted page\n${signInUrl}&state=<random>&code_challenge=<S256 of your verifier>\n\n# 2. they come back to your redirect with ?code=…&state=…\n# 3. exchange it (publishable key; one use, two minutes)\nPOST ${base}/auth/v1/token/exchange\napikey: <publishable key>\n{ "code": "<code>", "code_verifier": "<your verifier>" }`}</pre>
              </div>
            </Card>
          </div>

          <div className="pages-preview">
            <div className="row spread" style={{ marginBottom: 10 }}>
              <Segmented options={PAGE_OPTIONS} value={page} onChange={setPage} />
              <a className="hint" href={`${base}/auth/v1/hosted/${env}/${page}`} target="_blank" rel="noopener noreferrer">Open the real page</a>
            </div>
            <div className="pages-frame">
              <iframe title="Sign-in page preview" src={previewUrl} sandbox="allow-scripts allow-same-origin" />
            </div>
            <p className="hint">The preview shows unsaved changes. Nothing is sent from it.</p>
          </div>
        </div>
      )}
    </Loading>
  );
}
