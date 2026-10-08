import { Link, router } from "@inertiajs/react";
import { useEffect, useMemo, useRef, useState } from "react";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, IconButton, KeyValue, Loading, Modal, PageHead, Section, Switch, TagInput, useAction } from "../../components/ui";
import { PolicyPicker } from "../../components/definitions/PolicyPicker";
import { del, envPath, patch, useApi } from "../../lib/api";

// Settings the platform itself reads (services/api/app/env_settings.py validates
// them; the gateway enforces ip_allowlist and maintenance; Angula reads realtime;
// the data plane reads public_docs and cors_origins). Anything else under
// `settings` is open-ended data flows and policies read as `$settings.<key>`.
const KNOWN_KEYS = ["public_docs", "cors_origins", "ip_allowlist", "maintenance", "realtime", "timezone", "currency", "locale", "description"];
const RESERVED_KEYS = ["deletion_pending"];
const BLANK_RULE = { pattern: "", subscribe: null, publish: null, presence: true, history: 50 };
const BLANK_MAINTENANCE = { enabled: false, message: "", retry_after: 300, allow_secret_keys: true };

const CURRENCIES = [
  ["NGN", "Nigerian naira"], ["GHS", "Ghanaian cedi"], ["KES", "Kenyan shilling"], ["ZAR", "South African rand"],
  ["UGX", "Ugandan shilling"], ["TZS", "Tanzanian shilling"], ["RWF", "Rwandan franc"], ["XOF", "West African CFA franc"],
  ["XAF", "Central African CFA franc"], ["EGP", "Egyptian pound"], ["MAD", "Moroccan dirham"], ["ETB", "Ethiopian birr"],
  ["ZMW", "Zambian kwacha"], ["USD", "US dollar"], ["EUR", "Euro"], ["GBP", "Pound sterling"],
];
const LOCALES = [
  ["en", "English"], ["en-NG", "English (Nigeria)"], ["en-GH", "English (Ghana)"], ["en-KE", "English (Kenya)"], ["en-ZA", "English (South Africa)"],
  ["fr", "French"], ["fr-CI", "French (Côte d’Ivoire)"], ["fr-SN", "French (Senegal)"], ["sw", "Swahili"], ["ha", "Hausa"],
  ["yo", "Yoruba"], ["ig", "Igbo"], ["am", "Amharic"], ["ar", "Arabic"], ["pt", "Portuguese"], ["pt-AO", "Portuguese (Angola)"],
];
const AFRICAN_ZONES = ["Africa/Lagos", "Africa/Accra", "Africa/Nairobi", "Africa/Johannesburg", "Africa/Cairo", "Africa/Casablanca", "Africa/Addis_Ababa", "Africa/Dar_es_Salaam", "Africa/Kampala", "Africa/Kigali", "Africa/Abidjan", "Africa/Dakar", "Africa/Lusaka", "UTC"];

function timezones() {
  let all = [];
  try { all = Intl.supportedValuesOf("timeZone"); } catch { /* older browsers fall back to the shortlist */ }
  return [...new Set([...AFRICAN_ZONES, ...all])];
}

const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

// "Default" in a PolicyPicker is represented as `null` so the control has
// something to show — but the backend only applies its own default when the
// key is *absent*, not when it's present and null. Drop null/undefined keys
// before they go over the wire so "Default" really means "not set".
function omitNulls(value) {
  if (Array.isArray(value)) return value.map(omitNulls);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) => [k, omitNulls(v)])
    );
  }
  return value;
}

/** What to send: changed keys with their new value, removed keys as null (the API clears a null). */
function diff(original, draft) {
  const out = {};
  for (const key of new Set([...Object.keys(original), ...Object.keys(draft)])) {
    if (RESERVED_KEYS.includes(key)) continue;
    const next = draft[key] === "" ? undefined : draft[key];
    if (same(original[key], next)) continue;
    out[key] = next === undefined ? null : omitNulls(next);
  }
  return out;
}

export default function Settings({ env }) {
  const path = envPath(env);
  const state = useApi(path);
  const policiesState = useApi(envPath(env, "/policies"));
  const usage = useApi("/usage", { params: { env } });
  const policyNames = (policiesState.data?.data || []).map((p) => p.name);
  return (
    <Layout title="Settings">
      <PageHead title="Settings" description={`Behaviour, access and defaults for the ${env} environment. Infrastructure credentials live in .env and Secrets, not here.`} />
      <Loading state={state}>
        {(data) => <SettingsBody key={data.version + ":" + JSON.stringify(data.settings)} env={env} path={path} data={data} usage={usage.data} policyNames={policyNames} reload={state.reload} />}
      </Loading>
    </Layout>
  );
}

function SettingsBody({ env, path, data, usage, policyNames, reload }) {
  const original = data.settings || {};
  const [draft, setDraft] = useState(original);
  const [active, setActive] = useState("general");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [run, busy] = useAction();

  const changes = useMemo(() => diff(original, draft), [original, draft]);
  const changedKeys = Object.keys(changes);
  const dirty = changedKeys.length > 0;
  const set = (patchValues) => setDraft((d) => ({ ...d, ...patchValues }));

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = async () => {
    if (await run(() => patch(path, { settings: changes }), "Settings saved")) reload();
  };

  const maintenance = { ...BLANK_MAINTENANCE, ...(draft.maintenance || {}) };
  const setMaintenance = (values) => set({ maintenance: { ...maintenance, ...values } });
  const realtime = draft.realtime || {};
  const setRealtime = (values) => set({ realtime: { ...realtime, ...values } });
  const rules = realtime.channels || [];
  const setRules = (channels) => setRealtime({ channels });

  const customKeys = Object.keys(draft).filter((k) => !KNOWN_KEYS.includes(k) && !RESERVED_KEYS.includes(k));
  const custom = Object.fromEntries(customKeys.map((k) => [k, draft[k]]));
  const setCustom = (next) => setDraft((d) => {
    const kept = Object.fromEntries(Object.entries(d).filter(([k]) => KNOWN_KEYS.includes(k) || RESERVED_KEYS.includes(k)));
    return { ...kept, ...next };
  });

  const badges = {
    maintenance: maintenance.enabled ? <Badge tone="yellow">on</Badge> : null,
  };

  const sections = [
    ["general", "General", "Name, timezone, currency, language"],
    ["access", "API & access", "Docs, CORS and secret-key IPs"],
    ["maintenance", "Maintenance", "Pause the public API"],
    ["realtime", "Realtime", "Channel and policy rules"],
    ["infrastructure", "Infrastructure", "Database, storage, mail"],
    ["limits", "Limits", "Quotas on this installation"],
    ["custom", "Custom values", "Your own $settings"],
    ["advanced", "Advanced", "Export, import, delete"],
  ];
  const title = sections.find(([key]) => key === active)?.[1];
  const editable = !["infrastructure", "limits", "advanced"].includes(active);

  return <div className="stack lg">
    {maintenance.enabled && !dirty && <div className="alert warn"><b>Maintenance mode is on.</b> Publishable keys get 503 responses{maintenance.allow_secret_keys ? "; secret keys still work" : " and so do secret keys"}.</div>}
    <div style={{ display: "flex", gap: 18, alignItems: "flex-start" }}>
      <nav className="card" style={{ width: 230, flexShrink: 0, padding: 8 }} aria-label="Settings sections">
        {sections.map(([key, label, description]) => <button key={key} type="button" onClick={() => setActive(key)} style={{ width: "100%", textAlign: "left", border: 0, borderRadius: 8, padding: "11px 10px", background: active === key ? "var(--accent-soft)" : "transparent", color: "inherit", cursor: "pointer" }}>
          <b style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>{label}{badges[key]}</b>
          <span className="hint" style={{ display: "block" }}>{description}</span>
        </button>)}
      </nav>
      <Card title={title} className="grow" actions={editable && <>
        {dirty && <Button size="sm" disabled={busy} onClick={() => setDraft(original)}>Discard</Button>}
        <Button variant="primary" size="sm" disabled={busy || !dirty} onClick={save}>{dirty ? `Save ${changedKeys.length} change${changedKeys.length === 1 ? "" : "s"}` : "Save changes"}</Button>
      </>}>
        {active === "general" && <div className="stack" style={{ gap: 16 }}>
          <Field label="Description" optional hint="What this environment is for. Shown to your team; flows can read it as $settings.description."><input maxLength={280} value={draft.description || ""} onChange={(e) => set({ description: e.target.value })} placeholder="Customer-facing production data" /></Field>
          <Field label="Timezone" hint="Flows, schedules and mail templates read this as $settings.timezone."><TimezoneInput value={draft.timezone || ""} onChange={(v) => set({ timezone: v })} /></Field>
          <div className="row" style={{ gap: 16, alignItems: "flex-start" }}>
            <Field label="Default currency" className="grow" hint="Read as $settings.currency. Amounts are stored in minor units."><select value={draft.currency || ""} onChange={(e) => set({ currency: e.target.value })}><option value="">Not set</option>{CURRENCIES.map(([code, name]) => <option key={code} value={code}>{code} — {name}</option>)}</select></Field>
            <Field label="Default language" className="grow" hint="Read as $settings.locale."><select value={draft.locale || ""} onChange={(e) => set({ locale: e.target.value })}><option value="">Not set</option>{LOCALES.map(([code, name]) => <option key={code} value={code}>{code} — {name}</option>)}</select></Field>
          </div>
        </div>}

        {active === "access" && <div className="stack" style={{ gap: 18 }}>
          <Switch checked={!!draft.public_docs} onChange={(v) => set({ public_docs: v })} label="Publish generated API docs" hint="Makes the OpenAPI reference public at /docs/v1. Operators can always see it from Studio either way." />
          <Field label="Allowed browser origins" hint="Sites allowed to call the gateway with a publishable key from JavaScript, like https://app.example.com. Leave empty to allow any origin."><TagInput value={draft.cors_origins || []} onChange={(v) => set({ cors_origins: v })} placeholder="https://app.example.com" /></Field>
          <Field label="Secret-key IP allowlist" hint="When set, secret keys only work from these addresses or ranges (for example your servers). Publishable keys are not affected. Leave empty to allow any address."><TagInput value={draft.ip_allowlist || []} onChange={(v) => set({ ip_allowlist: v })} placeholder="203.0.113.7 or 10.0.0.0/8" /></Field>
          {(draft.ip_allowlist || []).length > 0 && <div className="alert warn">Make sure your own server addresses are on the list before saving. Requests from anywhere else will get 403 until you change it here, and Studio is not affected. Changes reach the gateway within about 30 seconds.</div>}
          <p className="muted" style={{ margin: 0 }}>Per-key restrictions (expiry, routes, addresses) are set on each key under <Link className="link-more" href={envHref(env, "keys")}>API keys</Link>.</p>
        </div>}

        {active === "maintenance" && <div className="stack" style={{ gap: 16 }}>
          <Switch checked={maintenance.enabled} onChange={(v) => setMaintenance({ enabled: v })} label="Maintenance mode" hint="The gateway answers with 503 and a Retry-After header instead of reaching your data. Applies within about 30 seconds." />
          <Field label="Message" hint="Returned to clients in the response body."><input maxLength={300} value={maintenance.message} onChange={(e) => setMaintenance({ message: e.target.value })} placeholder="We are upgrading the database and will be back shortly." /></Field>
          <Field label="Retry after (seconds)" hint="Sent as the Retry-After header so well-behaved clients back off."><input type="number" min={1} max={86400} value={maintenance.retry_after} onChange={(e) => setMaintenance({ retry_after: Math.min(86400, Math.max(1, Number(e.target.value) || 300)) })} /></Field>
          <Switch checked={maintenance.allow_secret_keys} onChange={(v) => setMaintenance({ allow_secret_keys: v })} label="Let secret keys through" hint="Keeps your servers, migrations and scripts working while end users see the maintenance response." />
        </div>}

        {active === "realtime" && <div className="stack" style={{ gap: 16 }}>
          <Switch checked={realtime.allow_client_publish !== false} onChange={(v) => setRealtime({ allow_client_publish: v })} label="Clients may publish" hint="Off restricts publishing to servers and flows; clients can still subscribe." />
          <Field label="Default policy" hint="Used by any channel that matches no rule below."><PolicyPicker value={realtime.default_policy ?? null} onChange={(v) => setRealtime({ default_policy: v })} policies={policyNames} nullLabel="Default (authenticated)" /></Field>
          <Section title="Channel rules" description="Rules are matched top to bottom. Patterns support {{ auth.org }} templates and * wildcards.">
            {rules.length === 0 && <p className="muted" style={{ margin: 0 }}>No rules yet — every channel uses the default policy.</p>}
            <div className="stack" style={{ gap: 10 }}>{rules.map((rule, i) => <div key={i} className="card sunken" style={{ padding: 14 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end" }}><Field label="Pattern" className="grow"><input value={rule.pattern || ""} onChange={(e) => setRules(rules.map((r, j) => j === i ? { ...r, pattern: e.target.value } : r))} placeholder="org:{{ auth.org }}" /></Field><IconButton icon="trash" label="Remove rule" onClick={() => setRules(rules.filter((_, j) => j !== i))} /></div>
              <div className="stack" style={{ gap: 10 }}><Field label="Subscribe"><PolicyPicker value={rule.subscribe ?? null} onChange={(v) => setRules(rules.map((r, j) => j === i ? { ...r, subscribe: v } : r))} policies={policyNames} nullLabel="Default (authenticated)" /></Field><Field label="Publish"><PolicyPicker value={rule.publish ?? null} onChange={(v) => setRules(rules.map((r, j) => j === i ? { ...r, publish: v } : r))} policies={policyNames} nullLabel="Default (authenticated)" /></Field><Switch checked={rule.presence !== false} onChange={(v) => setRules(rules.map((r, j) => j === i ? { ...r, presence: v } : r))} label="Presence" /><Field label="History depth" hint="Messages kept for late subscribers."><input type="number" min={0} max={1000} value={rule.history ?? 50} onChange={(e) => setRules(rules.map((r, j) => j === i ? { ...r, history: Number(e.target.value) || 0 } : r))} /></Field></div>
            </div>)}</div>
            <Button size="sm" onClick={() => setRules([...rules, { ...BLANK_RULE }])}><Icon name="plus" />Add channel rule</Button>
          </Section>
        </div>}

        {active === "infrastructure" && <Infrastructure env={env} infra={data.infra || {}} />}
        {active === "limits" && <Limits usage={usage} />}

        {active === "custom" && <Field label="Custom values" hint="Extra keys flows and policies can read as $settings.<key>. They are not platform configuration. Names use letters, digits and underscores.">
          <KeyValue value={custom} onChange={setCustom} keyLabel="Key" valueLabel="Value" addLabel="Add setting" />
        </Field>}

        {active === "advanced" && <Advanced env={env} path={path} data={data} settings={original} draftDirty={dirty} onImport={(incoming) => { setDraft((d) => ({ ...d, ...incoming })); setActive("general"); }} onDelete={() => setConfirmDelete(true)} reload={reload} />}
      </Card>
    </div>
    {confirmDelete && <DeleteEnvironment env={env} path={path} onClose={() => setConfirmDelete(false)} onDeleted={() => router.visit("/environments")} />}
  </div>;
}

function TimezoneInput({ value, onChange }) {
  const zones = useMemo(timezones, []);
  const id = useRef(`tz-${Math.random().toString(36).slice(2)}`).current;
  return <>
    <input list={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder="Africa/Lagos" autoComplete="off" />
    <datalist id={id}>{zones.map((z) => <option key={z} value={z} />)}</datalist>
  </>;
}

function Infrastructure({ env, infra }) {
  const storage = infra.storage || {};
  const mail = infra.mail || {};
  const rows = [
    ["Database", infra.database_url ? "Own database" : "Platform default", Boolean(infra.database_url), "database", "Resources are stored in the shared Postgres, in a schema for this environment, unless you point the environment at your own database."],
    ["Storage", storage.driver || "local", true, "storage", "Where uploaded objects live. Buckets and access policies are managed on the Storage page."],
    ["Mail", mail.host ? (mail.suppress ? "Suppressed" : mail.host) : "Not configured", Boolean(mail.host) && !mail.suppress, "mail", "SMTP used for flows, auth emails and notifications. Unconfigured mail is logged and suppressed, never sent."],
    ["Secrets", "Encrypted at rest", true, "secrets", "Credentials are referenced as secret://NAME and never shown after saving."],
  ];
  return <div className="stack" style={{ gap: 12 }}>
    <p className="muted" style={{ margin: 0 }}>A read-only summary. The installation-wide pieces (Redis, JWT secrets, master key, quotas) are set once through .env.</p>
    {rows.map(([label, value, ok, section, hint]) => <div key={label} className="card sunken" style={{ padding: 14 }}>
      <div className="spread">
        <div><b>{label}</b> <Badge tone={ok ? "green" : "yellow"}>{value}</Badge></div>
        <Link className="link-more" href={envHref(env, section)}>Manage <Icon name="chevronRight" size={14} /></Link>
      </div>
      <p className="muted" style={{ margin: "6px 0 0" }}>{hint}</p>
    </div>)}
  </div>;
}

function bytes(n) {
  if (!n) return "No limit";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${Math.round(n * 10) / 10} ${units[i]}`;
}

function Limits({ usage }) {
  if (!usage) return <p className="muted" style={{ margin: 0 }}>Loading limits…</p>;
  const count = (u) => (u.limit ? `${u.used ?? 0} of ${u.limit}` : `${u.used ?? 0} · no limit`);
  const rows = [
    ["Environments", count(usage.environments || {})],
    ["API keys in this environment", count(usage.api_keys || {})],
    ["Largest upload", bytes(usage.uploads?.limit)],
    ["Active users", usage.users?.limit ? `up to ${usage.users.limit}` : "No limit"],
  ];
  return <div className="stack" style={{ gap: 10 }}>
    <p className="muted" style={{ margin: 0 }}>Limits are set for the whole installation through PAWABASE_* variables in .env, so they apply to every environment.</p>
    {rows.map(([label, value]) => <div key={label} className="spread card sunken" style={{ padding: "12px 14px" }}><b>{label}</b><span>{value}</span></div>)}
  </div>;
}

function Advanced({ env, path, data, settings, draftDirty, onImport, onDelete, reload }) {
  const [run, busy] = useAction();
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const exported = useMemo(() => JSON.stringify(Object.fromEntries(Object.entries(settings).filter(([k]) => !RESERVED_KEYS.includes(k))), null, 2), [settings]);
  const download = () => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([exported + "\n"], { type: "application/json" }));
    link.download = `${env}.settings.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  const load = () => {
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Expected a JSON object");
      const { deletion_pending, ...incoming } = parsed; // eslint-disable-line no-unused-vars
      setError("");
      onImport(incoming);
    } catch (e) {
      setError(e.message);
    }
  };
  return <div className="stack" style={{ gap: 22 }}>
    <Section title="Environment" description="The default environment answers requests that name no environment.">
      <div className="spread">
        <div className="row">{data.is_default ? <Badge tone="green">default environment</Badge> : <Button disabled={busy} onClick={async () => { if (await run(() => patch(path, { is_default: true }), "Now the default")) reload(); }}>Make default</Button>}<span className="muted">Definitions version {data.version}</span></div>
        <Link className="link-more" href={envHref(env, "releases")}>Releases <Icon name="chevronRight" size={14} /></Link>
      </div>
    </Section>
    <Section title="Export and import" description="Move settings between environments. Importing fills the form; nothing changes until you review it and press Save.">
      <div className="stack" style={{ gap: 10 }}>
        <div className="row"><Button size="sm" onClick={download}><Icon name="upload" />Download settings.json</Button></div>
        <Field label="Import settings" hint={draftDirty ? "You have unsaved changes; importing merges over them." : "Paste an exported settings.json. Keys not in the file are left alone."} error={error}>
          <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder='{"timezone": "Africa/Lagos", "currency": "NGN"}' spellCheck={false} />
        </Field>
        <div className="row"><Button size="sm" disabled={!text.trim()} onClick={load}>Load into form</Button></div>
      </div>
    </Section>
    {!data.is_default && <Section title="Danger zone" description="Deleting an environment removes its data, keys, secrets and users for good.">
      <Button variant="danger" onClick={onDelete}>Delete environment</Button>
    </Section>}
  </div>;
}

function DeleteEnvironment({ env, path, onClose, onDeleted }) {
  const [value, setValue] = useState("");
  const [run, busy] = useAction();
  const ready = value === env;
  return <Modal title={`Delete ${env}?`} onClose={onClose} footer={<><Button onClick={onClose} disabled={busy}>Cancel</Button><Button variant="danger" disabled={!ready || busy} onClick={async () => { if (await run(() => del(path), "Environment deleted")) onDeleted(); }}>Permanently delete</Button></>}>
    <div className="alert danger"><b>This cannot be undone.</b> The environment’s resource data, API keys, secrets and users are removed permanently.</div>
    <Field label={`Type ${env} to confirm`}><input autoFocus value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" /></Field>
  </Modal>;
}
