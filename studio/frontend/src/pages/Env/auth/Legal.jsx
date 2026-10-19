import { Card, Field, Loading } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "./shared";

const PRESETS = [["terms", "Terms of Service"], ["privacy", "Privacy Policy"], ["cookies", "Cookie Policy"], ["dpa", "Data Processing Agreement"], ["aup", "Acceptable Use Policy"]];

/** The documents people are shown, and which they must accept to create an account. */
export default function Legal({ env }) {
  const config = useAuthConfig(env);
  const legal = { documents: [], version: "", notice: "", ...(config.value.legal || {}) };
  const set = (values) => config.set({ legal: { ...legal, ...values } });
  const docs = legal.documents || [];
  const change = (index, values) => set({ documents: docs.map((d, i) => (i === index ? { ...d, ...values } : d)) });
  const add = (key, label) => {
    const taken = new Set(docs.map((d) => d.key));
    let name = key; for (let n = 2; taken.has(name); n += 1) name = `${key}-${n}`;
    set({ documents: [...docs, { key: name, label, url: "", required: key === "terms" || key === "privacy" }] });
  };
  const required = docs.filter((d) => d.required);
  return (
    <Loading state={config.state}>
      {() => (
        <div className="stack lg">
          <SettingsPanel title="Legal documents" description="Link to your terms, privacy policy and anything else people should see. Every document is linked at the foot of the sign-in screens; a required one is a box to tick when creating an account, on the hosted page and in the sign-up API (accepted_terms)." config={config}>
            <div className="stack">
              {docs.length === 0 && <p className="hint" style={{ margin: 0 }}>No documents yet. Add your terms and privacy policy to get started.</p>}
              {docs.map((doc, index) => (
                <div key={doc.key} className="q-row">
                  <div className="row">
                    <input value={doc.label} onChange={(e) => change(index, { label: e.target.value })} placeholder="Name" aria-label="Name" maxLength={60} />
                    <input value={doc.url} onChange={(e) => change(index, { url: e.target.value })} placeholder="https://acme.com/terms" aria-label="Address" style={{ flex: 2 }} />
                    <label className="check" style={{ whiteSpace: "nowrap" }}><input type="checkbox" checked={!!doc.required} onChange={(e) => change(index, { required: e.target.checked })} /> Must accept</label>
                    <button type="button" className="btn sm" onClick={() => set({ documents: docs.filter((_, i) => i !== index) })}>Remove</button>
                  </div>
                </div>
              ))}
              <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
                {PRESETS.filter(([key]) => !docs.some((d) => d.key === key)).map(([key, label]) => <button key={key} type="button" className="btn sm" disabled={docs.length >= 8} onClick={() => add(key, label)}>Add {label}</button>)}
                <button type="button" className="btn sm" disabled={docs.length >= 8} onClick={() => add("document", "New document")}>Add another</button>
              </div>
            </div>
          </SettingsPanel>

          <SettingsPanel title="Consent record" description="When someone accepts, Pawabase records which documents, which version and when, on their account (app metadata, so they cannot edit it)." config={config}>
            <Field label="Version" hint="Anything you like: a date or a number. Change it when your terms change, so you can tell who accepted which.">
              <input value={legal.version} onChange={(e) => set({ version: e.target.value })} placeholder="2026-10-01" maxLength={40} style={{ maxWidth: 260 }} />
            </Field>
            {required.length > 0 && <p className="hint" style={{ margin: 0 }}>Sign-up will ask people to agree to: {required.map((d) => d.label).join(", ")}.</p>}
          </SettingsPanel>
        </div>
      )}
    </Loading>
  );
}
