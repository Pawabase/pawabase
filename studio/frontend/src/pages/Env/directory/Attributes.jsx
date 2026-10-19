import { Card, Field, Loading, Segmented } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "../auth/shared";

const ATTRIBUTES = [
  ["given_name", "First name"], ["family_name", "Last name"], ["name", "Full name"], ["username", "Username"],
  ["phone", "Phone number"], ["birthdate", "Date of birth"], ["gender", "Gender"], ["company", "Company"],
  ["job_title", "Job title"], ["website", "Website"], ["country", "Country"], ["address", "Address"],
];
const MODES = [["off", "Off"], ["optional", "Optional"], ["required", "Required"]];
const KINDS = [["text", "Short text"], ["textarea", "Long text"], ["number", "Number"], ["date", "Date"], ["tel", "Phone"], ["url", "Web address"], ["select", "Choice"], ["checkbox", "Checkbox"]];
const DEFAULT_FORM = { sign_in_with: ["email"], attributes: {}, custom: [], locked: [], claims: [], steps: "auto" };

function slug(label, taken) {
  const base = (label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "field").replace(/^[0-9]/, "f_$&").slice(0, 28);
  let key = base;
  for (let n = 2; taken.has(key); n += 1) key = `${base}_${n}`;
  return key;
}

const toggle = (list, key, on) => (on ? [...new Set([...list, key])] : list.filter((k) => k !== key));

/** The attributes a person has: which are asked at sign-up, which they may change later, and which ride in their token. */
export default function Attributes({ env }) {
  const config = useAuthConfig(env);
  const form = { ...DEFAULT_FORM, ...(config.value.signup_form || {}) };
  const set = (values) => config.set({ signup_form: { ...form, ...values } });
  const identifiers = form.sign_in_with || ["email"];
  const forced = (key) => identifiers.includes(key) && (key === "username" || key === "phone");
  const modeOf = (key) => (forced(key) ? "required" : form.attributes?.[key] || "off");
  const custom = form.custom || [];
  const setCustom = (list) => set({ custom: list });
  const locked = form.locked || [];
  const claims = form.claims || [];
  const listed = (config.value.directory?.claims?.mode || "all") === "listed";

  const Flags = ({ attrKey }) => (
    <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 14 }}>
      <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui small text-ui-sm" title="Only an administrator can change it once it has a value"><input type="checkbox" checked={locked.includes(attrKey)} onChange={(e) => set({ locked: toggle(locked, attrKey, e.target.checked) })} /> Admin-only edits</label>
      {listed && <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui small text-ui-sm" title="Include this attribute in the access token"><input type="checkbox" checked={claims.includes(attrKey)} onChange={(e) => set({ claims: toggle(claims, attrKey, e.target.checked) })} /> In token</label>}
    </span>
  );

  return (
    <Loading state={config.state}>
      {() => (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
          <SettingsPanel title="Standard attributes" description="Choose, for each detail, whether sign-up asks for it. Email and password are always asked. Everything is enforced on the sign-up API as well as the hosted page." config={config}>
            <div className="attr-list flex flex-col">
              {ATTRIBUTES.map(([key, label]) => (
                <div key={key} className="attr-row flex items-center justify-between gap-4 p-[12px_0] [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0 [&.fixed_span:first-child]:text-muted">
                  <span className="attr-name flex-1">{label}</span>
                  <Flags attrKey={key} />
                  {forced(key)
                    ? <span className="hint text-xs font-book text-muted leading-[1.45]">Required: used to sign in</span>
                    : <Segmented options={MODES} value={modeOf(key)} onChange={(mode) => set({ attributes: { ...(form.attributes || {}), [key]: mode } })} />}
                </div>
              ))}
            </div>
          </SettingsPanel>

          <Card
            title="Custom attributes"
            actions={<button type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text sm" disabled={custom.length >= 12} onClick={() => {
              const taken = new Set(custom.map((f) => f.key));
              setCustom([...custom, { key: slug("New attribute", taken), label: "New attribute", type: "text", required: false }]);
            }}>Add an attribute</button>}
          >
            <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
              <p className="muted text-muted" style={{ margin: 0 }}>Your own fields, stored on the person as <code>user_metadata</code> under the key you give them. Up to 12. Save from the bar above.</p>
              {custom.length === 0 && <p className="hint text-xs font-book text-muted leading-[1.45]">No custom attributes yet.</p>}
              {custom.map((field, index) => {
                const change = (values) => setCustom(custom.map((f, i) => (i === index ? { ...f, ...values } : f)));
                return (
                  <div key={index} className="q-row flex flex-col gap-2 p-3 [border:1px_solid_var(--line)] rounded-sm">
                    <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                      <input value={field.label} onChange={(e) => change({ label: e.target.value })} placeholder="Label" aria-label="Label" />
                      <input value={field.key} onChange={(e) => change({ key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "") })} placeholder="key" aria-label="Key" style={{ maxWidth: 170, fontFamily: "var(--mono)" }} />
                      <select value={field.type} onChange={(e) => change({ type: e.target.value })} aria-label="Type" style={{ maxWidth: 140 }}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                      <button type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text sm" onClick={() => setCustom(custom.filter((_, i) => i !== index))}>Remove</button>
                    </div>
                    <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ flexWrap: "wrap", gap: 16 }}>
                      <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui small text-ui-sm"><input type="checkbox" checked={field.ask !== false} onChange={(e) => change({ ask: e.target.checked })} /> Ask at sign-up</label>
                      <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui small text-ui-sm"><input type="checkbox" checked={!!field.required} onChange={(e) => change({ required: e.target.checked })} /> Required</label>
                      <Flags attrKey={field.key} />
                    </div>
                    {field.type === "select" && <input value={(field.options || []).join(", ")} onChange={(e) => change({ options: e.target.value.split(",").map((o) => o.trim()).filter(Boolean) })} placeholder="Choices, separated by commas" aria-label="Choices" />}
                    {["text", "textarea", "number"].includes(field.type) && (
                      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
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

          <SettingsPanel title="Access token contents" description="Which profile attributes are copied into the access token, so policies and your API can read them without a lookup." config={config}>
            <Field label="Attributes in the token">
              <Segmented options={[["all", "Everything on the profile"], ["listed", "Only attributes marked “In token”"]]} value={config.value.directory?.claims?.mode || "all"} onChange={(mode) => config.set({ directory: { ...(config.value.directory || {}), claims: { mode } } })} />
            </Field>
            <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>Profile attributes can be edited by the person, so policies should not rely on them for access decisions. Use roles or app metadata for that.</p>
          </SettingsPanel>
        </div>
      )}
    </Loading>
  );
}
