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
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
          <SettingsPanel icon="book" title="Legal documents" description="Link to your terms, privacy policy and anything else people should see. Every document is linked at the foot of the sign-in screens; a required one is a box to tick when creating an account, on the hosted page and in the sign-up API (accepted_terms)." config={config}>
            <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
              {docs.length === 0 && <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>No documents yet. Add your terms and privacy policy to get started.</p>}
              {docs.map((doc, index) => (
                <div key={doc.key} className="q-row flex flex-col gap-2 p-3 [border:1px_solid_var(--line)] rounded-sm">
                  <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                    <input value={doc.label} onChange={(e) => change(index, { label: e.target.value })} placeholder="Name" aria-label="Name" maxLength={60} />
                    <input value={doc.url} onChange={(e) => change(index, { url: e.target.value })} placeholder="https://acme.com/terms" aria-label="Address" style={{ flex: 2 }} />
                    <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui" style={{ whiteSpace: "nowrap" }}><input type="checkbox" checked={!!doc.required} onChange={(e) => change(index, { required: e.target.checked })} /> Must accept</label>
                    <button type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text sm" onClick={() => set({ documents: docs.filter((_, i) => i !== index) })}>Remove</button>
                  </div>
                </div>
              ))}
              <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ flexWrap: "wrap", gap: 8 }}>
                {PRESETS.filter(([key]) => !docs.some((d) => d.key === key)).map(([key, label]) => <button key={key} type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text sm" disabled={docs.length >= 8} onClick={() => add(key, label)}>Add {label}</button>)}
                <button type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text sm" disabled={docs.length >= 8} onClick={() => add("document", "New document")}>Add another</button>
              </div>
            </div>
          </SettingsPanel>

          <SettingsPanel icon="audit" title="Consent record" description="When someone accepts, Pawabase records which documents, which version and when, on their account (app metadata, so they cannot edit it)." config={config}>
            <Field label="Version" hint="Anything you like: a date or a number. Change it when your terms change, so you can tell who accepted which.">
              <input value={legal.version} onChange={(e) => set({ version: e.target.value })} placeholder="2026-10-01" maxLength={40} style={{ maxWidth: 260 }} />
            </Field>
            {required.length > 0 && <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>Sign-up will ask people to agree to: {required.map((d) => d.label).join(", ")}.</p>}
          </SettingsPanel>
        </div>
      )}
    </Loading>
  );
}
