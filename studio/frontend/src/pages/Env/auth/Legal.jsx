import { Badge, Button, Field, Loading, Segmented } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "./shared";

const PRESETS = [
  ["terms", "Terms of Service", "The rules for using your product.", true],
  ["privacy", "Privacy Policy", "What you collect and why.", true],
  ["cookies", "Cookie Policy", "Cookies and similar storage.", false],
  ["dpa", "Data Processing Agreement", "For business customers who need one.", false],
  ["aup", "Acceptable Use Policy", "What people may not do.", false],
];
const ABOUT = Object.fromEntries(PRESETS.map(([key, , about]) => [key, about]));

const validUrl = (value) => { try { const u = new URL(value); return u.protocol === "https:" || u.hostname === "localhost"; } catch { return false; } };

/** What a person is shown when they create an account, built from the documents beside it. */
function Consent({ docs, notice }) {
  const required = docs.filter((d) => d.required);
  const linked = docs.filter((d) => !d.required);
  const names = (list) => list.map((d, i) => <span key={d.key}>{i > 0 ? (i === list.length - 1 ? " and " : ", ") : ""}<u>{d.label || "Untitled"}</u></span>);
  return (
    <div className="rounded-sm border border-line-2 bg-surface p-5">
      <div className="mb-3 text-ui-sm font-medium-plus text-muted">What people see when they sign up</div>
      <div className="flex flex-col gap-3 rounded-sm border border-line p-4 text-ui">
        <div className="h-9 rounded-sm border border-line bg-sunken" aria-hidden="true" />
        <div className="h-9 rounded-sm border border-line bg-sunken" aria-hidden="true" />
        {required.length > 0
          ? <label className="flex items-start gap-2 text-ui-sm"><input type="checkbox" readOnly checked={false} /><span>I agree to the {names(required)}.</span></label>
          : <p className="hint text-xs text-muted" style={{ margin: 0 }}>Nothing is required yet, so sign-up has no checkbox.</p>}
        <div className="grid h-9 place-items-center rounded-sm bg-brand text-ui-sm font-medium-plus text-white">Create account</div>
        {linked.length > 0 && <p className="m-0 text-center text-xs text-muted">{linked.map((d, i) => <span key={d.key}>{i > 0 ? " · " : ""}<u>{d.label || "Untitled"}</u></span>)}</p>}
      </div>
      {notice && <p className="hint text-xs text-muted" style={{ marginBottom: 0 }}>{notice}</p>}
    </div>
  );
}

/** The documents people are shown, and which they must accept to create an account. */
export default function Legal({ env }) {
  const config = useAuthConfig(env);
  const legal = { documents: [], version: "", notice: "", ...(config.value.legal || {}) };
  const set = (values) => config.set({ legal: { ...legal, ...values } });
  const docs = legal.documents || [];
  const change = (index, values) => set({ documents: docs.map((d, i) => (i === index ? { ...d, ...values } : d)) });
  const add = (key, label, required = false) => {
    const taken = new Set(docs.map((d) => d.key));
    let name = key; for (let n = 2; taken.has(name); n += 1) name = `${key}-${n}`;
    set({ documents: [...docs, { key: name, label, url: "", required }] });
  };
  const missing = PRESETS.filter(([key]) => !docs.some((d) => d.key === key));
  const incomplete = docs.filter((d) => !d.url || !validUrl(d.url)).length;
  const today = new Date().toISOString().slice(0, 10);

  return (
    <Loading state={config.state}>
      {() => (
        <div className="grid grid-cols-[minmax(0,_1.3fr)_minmax(0,_1fr)] gap-6 items-start max-layout:grid-cols-1">
          <div className="flex flex-col gap-3">
            <SettingsPanel icon="book" title="Documents" description="Every document is linked at the foot of the sign-in pages. A required one becomes a box to tick when creating an account, on the hosted page and in the sign-up API (accepted_terms)." config={config}>
              {docs.length === 0 && (
                <div className="rounded-sm border border-dashed border-line-2 p-6 text-center">
                  <b className="text-ui">Start with the two most people need.</b>
                  <p className="hint text-xs text-muted">Add your terms and privacy policy, then paste where each one lives.</p>
                  <Button size="sm" variant="primary" onClick={() => set({ documents: [
                    { key: "terms", label: "Terms of Service", url: "", required: true },
                    { key: "privacy", label: "Privacy Policy", url: "", required: true },
                  ] })}>Add both</Button>
                </div>
              )}
              {docs.map((doc, index) => {
                const ok = doc.url && validUrl(doc.url);
                return (
                  <div key={doc.key} className="flex flex-col gap-3 rounded-sm border border-line p-4">
                    <div className="flex items-center justify-between gap-3">
                      <input value={doc.label} onChange={(e) => change(index, { label: e.target.value })} placeholder="Name" aria-label="Name" maxLength={60} style={{ fontWeight: 600, maxWidth: 280 }} />
                      <div className="flex items-center gap-2">
                        {doc.url ? <Badge tone={ok ? "green" : "red"}>{ok ? "https link" : "needs https"}</Badge> : <Badge>no link yet</Badge>}
                        <Button size="sm" onClick={() => set({ documents: docs.filter((_, i) => i !== index) })}>Remove</Button>
                      </div>
                    </div>
                    <input value={doc.url} onChange={(e) => change(index, { url: e.target.value })} placeholder="https://acme.com/terms" aria-label="Address" />
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="hint text-xs text-muted">{ABOUT[doc.key.replace(/-\d+$/, "")] || "Shown at the foot of every sign-in page."}</span>
                      <Segmented options={[["yes", "Must accept"], ["no", "Just linked"]]} value={doc.required ? "yes" : "no"} onChange={(v) => change(index, { required: v === "yes" })} />
                    </div>
                  </div>
                );
              })}
              {docs.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {missing.map(([key, label, , required]) => <Button key={key} size="sm" disabled={docs.length >= 8} onClick={() => add(key, label, required)}>+ {label}</Button>)}
                  <Button size="sm" disabled={docs.length >= 8} onClick={() => add("document", "New document")}>+ Another</Button>
                </div>
              )}
            </SettingsPanel>

            <SettingsPanel icon="audit" title="Consent record" description="When someone accepts, Pawabase records which documents, which version and when, on their account (app metadata, so they cannot edit it)." config={config}>
              <Field label="Version" hint="A date or a number. Change it when your terms change, so you can tell who accepted which.">
                <div className="flex flex-wrap items-center gap-2">
                  <input value={legal.version} onChange={(e) => set({ version: e.target.value })} placeholder={today} maxLength={40} style={{ maxWidth: 220 }} />
                  <Button size="sm" onClick={() => set({ version: today })}>Use today's date</Button>
                </div>
              </Field>
              {incomplete > 0 && <p className="hint text-xs text-muted" style={{ margin: 0 }}>{incomplete} document{incomplete === 1 ? " has" : "s have"} no working https link yet.</p>}
            </SettingsPanel>
          </div>

          <div className="sticky top-4 max-layout:static"><Consent docs={docs} notice={legal.version ? `Consent is recorded against version ${legal.version}.` : ""} /></div>
        </div>
      )}
    </Loading>
  );
}
