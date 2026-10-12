import { usePage } from "@inertiajs/react";
import { useMemo } from "react";
import { Card, Field, Loading, Segmented } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "./shared";

const ATTRIBUTES = [
  ["given_name", "First name"], ["family_name", "Last name"], ["name", "Full name"], ["username", "Username"],
  ["phone", "Phone number"], ["birthdate", "Date of birth"], ["gender", "Gender"], ["company", "Company"],
  ["job_title", "Job title"], ["website", "Website"], ["country", "Country"], ["address", "Address"],
];
const IDENTIFIERS = [["email", "Email"], ["username", "Username"], ["phone", "Phone number"]];
const MODES = [["off", "Off"], ["optional", "Optional"], ["required", "Required"]];
const KINDS = [["text", "Short text"], ["textarea", "Long text"], ["number", "Number"], ["date", "Date"], ["tel", "Phone"], ["url", "Web address"], ["select", "Choice"], ["checkbox", "Checkbox"]];
const DEFAULT_FORM = { sign_in_with: ["email"], attributes: {}, custom: [], steps: "auto" };

function encode(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  let binary = "";
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function slug(label, taken) {
  const base = (label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "field").replace(/^[0-9]/, "f_$&").slice(0, 28);
  let key = base;
  for (let n = 2; taken.has(key); n += 1) key = `${base}_${n}`;
  return key;
}

/** What sign-up asks for and which identifiers sign in, with a live preview of the hosted sign-up page. */
export default function SignupForm({ env }) {
  const config = useAuthConfig(env);
  const { gateway_url: gatewayUrl } = usePage().props;
  const form = { ...DEFAULT_FORM, ...(config.value.signup_form || {}) };
  const set = (values) => config.set({ signup_form: { ...form, ...values } });
  const base = (gatewayUrl || "").replace(/\/$/, "");
  const previewUrl = useMemo(
    () => `${base}/auth/v1/hosted/${env}/preview/signup?b=${encode(config.value.branding || {})}&f=${encode(config.value.signup_form || {})}`,
    [base, env, config.value.branding, config.value.signup_form],
  );
  const identifiers = form.sign_in_with || ["email"];
  const forced = (key) => (key === "username" && identifiers.includes("username")) || (key === "phone" && identifiers.includes("phone"));
  const modeOf = (key) => (forced(key) ? "required" : form.attributes?.[key] || "off");
  const setMode = (key, mode) => set({ attributes: { ...(form.attributes || {}), [key]: mode } });
  const toggleIdentifier = (key) => {
    if (key === "email") return;
    const next = identifiers.includes(key) ? identifiers.filter((i) => i !== key) : [...identifiers, key];
    set({ sign_in_with: ["email", ...IDENTIFIERS.map(([k]) => k).filter((k) => k !== "email" && next.includes(k))] });
  };
  const custom = form.custom || [];
  const setCustom = (list) => set({ custom: list });

  return (
    <Loading state={config.state}>
      {() => (
        <div className="pages-grid">
          <div className="stack lg">
            <SettingsPanel title="Sign in with" description="Which identifiers a person can type on the sign-in page. Email is always collected, because that is where verification and recovery go." config={config}>
              <div className="stack">
                {IDENTIFIERS.map(([key, label]) => (
                  <label key={key} className="check">
                    <input type="checkbox" checked={identifiers.includes(key)} disabled={key === "email"} onChange={() => toggleIdentifier(key)} />
                    <span>{label}{key === "email" ? " (always)" : ""}</span>
                  </label>
                ))}
                <p className="hint" style={{ margin: 0 }}>A username or phone number you allow for sign-in becomes required at sign-up, and must be unique.</p>
              </div>
            </SettingsPanel>

            <SettingsPanel title="What sign-up asks" description="Pick each detail: not asked, optional or required. Everything is also enforced on the sign-up API, so a required answer is required everywhere." config={config}>
              <div className="attr-list">
                <div className="attr-row fixed"><span>Email</span><span className="hint">Always asked</span></div>
                <div className="attr-row fixed"><span>Password</span><span className="hint">Always asked</span></div>
                {ATTRIBUTES.map(([key, label]) => (
                  <div key={key} className="attr-row">
                    <span>{label}</span>
                    {forced(key)
                      ? <span className="hint">Required: used to sign in</span>
                      : <Segmented options={MODES} value={modeOf(key)} onChange={(mode) => setMode(key, mode)} />}
                  </div>
                ))}
              </div>
              <Field label="Form layout" hint="Two steps puts the account on the first screen and the rest of the details on the second. Auto does that when more than two details are asked.">
                <Segmented options={[["auto", "Auto"], ["single", "One page"], ["two", "Two steps"]]} value={form.steps} onChange={(v) => set({ steps: v })} />
              </Field>
            </SettingsPanel>

            <Card
              title="Your own questions"
              actions={<button type="button" className="btn sm" disabled={custom.length >= 12} onClick={() => {
                const taken = new Set(custom.map((f) => f.key));
                setCustom([...custom, { key: slug("New question", taken), label: "New question", type: "text", required: false }]);
              }}>Add a question</button>}
            >
              <div className="stack">
                <p className="muted" style={{ margin: 0 }}>Answers are saved on the person as <code>user_metadata</code>. Up to 12.</p>
                {custom.length === 0 && <p className="hint">No custom questions yet.</p>}
                {custom.map((field, index) => {
                  const change = (values) => setCustom(custom.map((f, i) => (i === index ? { ...f, ...values } : f)));
                  return (
                    <div key={field.key} className="q-row">
                      <div className="row">
                        <input value={field.label} onChange={(e) => change({ label: e.target.value })} placeholder="Question" aria-label="Question" />
                        <select value={field.type} onChange={(e) => change({ type: e.target.value })} aria-label="Answer type" style={{ maxWidth: 150 }}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                        <label className="check" style={{ whiteSpace: "nowrap" }}><input type="checkbox" checked={!!field.required} onChange={(e) => change({ required: e.target.checked })} /> Required</label>
                        <button type="button" className="btn sm" onClick={() => setCustom(custom.filter((_, i) => i !== index))} aria-label="Remove question">Remove</button>
                      </div>
                      {field.type === "select" && <input value={(field.options || []).join(", ")} onChange={(e) => change({ options: e.target.value.split(",").map((o) => o.trim()).filter(Boolean) })} placeholder="Choices, separated by commas" aria-label="Choices" />}
                      {["text", "textarea", "number"].includes(field.type) && (
                        <div className="row">
                          <input type="number" value={field.min ?? ""} onChange={(e) => change({ min: e.target.value === "" ? undefined : Number(e.target.value) })} placeholder={field.type === "number" ? "Smallest value" : "Fewest characters"} aria-label="Minimum" />
                          <input type="number" value={field.max ?? ""} onChange={(e) => change({ max: e.target.value === "" ? undefined : Number(e.target.value) })} placeholder={field.type === "number" ? "Largest value" : "Most characters"} aria-label="Maximum" />
                        </div>
                      )}
                      <input value={field.help || ""} onChange={(e) => change({ help: e.target.value })} placeholder="Help text (optional)" aria-label="Help text" />
                    </div>
                  );
                })}
              </div>
            </Card>
          </div>

          <div className="pages-preview">
            <div className="row spread" style={{ marginBottom: 10 }}>
              <span className="eyebrow">Sign-up page</span>
              <a className="hint" href={`${base}/auth/v1/hosted/${env}/signup`} target="_blank" rel="noopener noreferrer">Open the real page</a>
            </div>
            <div className="pages-frame">
              <iframe title="Sign-up page preview" src={previewUrl} sandbox="allow-scripts allow-same-origin" />
            </div>
            <p className="hint">The preview shows unsaved changes. Nothing is sent from it.</p>
          </div>
        </div>
      )}
    </Loading>
  );
}
