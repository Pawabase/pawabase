import { Link, router } from "@inertiajs/react";
import { useMemo, useRef, useState } from "react";
import Layout, { envHref, useSection } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, Loading, Modal, Section, useAction } from "../../components/ui";
import { SaveButtons, RESERVED_KEYS, useEnvSettings } from "../../components/envSettings";
import { del, envPath, patch } from "../../lib/api";

// What this environment is called and how it speaks to your users. How it is run (databases, storage, limits, the gateway)
// is not set here: Pawabase operates that, and what you control about your API is under Networking.

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

function TimezoneInput({ value, onChange }) {
  const zones = useMemo(timezones, []);
  const id = useRef(`tz-${Math.random().toString(36).slice(2)}`).current;
  return <>
    <input list={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder="Africa/Lagos" autoComplete="off" />
    <datalist id={id}>{zones.map((z) => <option key={z} value={z} />)}</datalist>
  </>;
}

const SETTINGS_SECTIONS = [
  ["general", "General", "settings"],
  ["app", "App details", "mail"],
  ["environment", "Environment", "networking"],
];

export default function Settings({ env }) {
  const [active, setActive] = useSection(SETTINGS_SECTIONS.map(([key]) => key), "general");
  const editor = useEnvSettings(env);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const section = SETTINGS_SECTIONS.find(([key]) => key === active);
  return (
    <Layout title="Settings" subnav={{ title: "Settings", groups: [{ items: SETTINGS_SECTIONS.map(([key, label, icon]) => ({ key, label, icon })) }], active, onSelect: setActive }}>
      <Loading state={editor.state}>
        {(data) => !editor.ready ? null : (
          <div className="mail-page" style={{ maxWidth: 760 }}>
            {data.settings?.deletion_pending && <div className="alert error" style={{ marginBottom: 16 }}><b>This environment is being deleted.</b> It will disappear shortly.</div>}
            {active === "general" && (
              <Card icon="settings" title="General" description="Defaults your flows, schedules and mail templates read." actions={<SaveButtons editor={editor} />}>
                <div className="stack">
                  <Field label="Description" optional hint="What this environment is for. Flows can read it as $settings.description."><input maxLength={280} value={editor.draft.description || ""} onChange={(e) => editor.set({ description: e.target.value })} placeholder="Customer-facing production data" /></Field>
                  <Field label="Timezone" hint="Schedules and emails use it. Read as $settings.timezone."><TimezoneInput value={editor.draft.timezone || ""} onChange={(v) => editor.set({ timezone: v })} /></Field>
                  <div className="row top" style={{ gap: 16 }}>
                    <Field label="Currency" className="grow" hint="Read as $settings.currency. Amounts are stored in minor units."><select value={editor.draft.currency || ""} onChange={(e) => editor.set({ currency: e.target.value })}><option value="">Not set</option>{CURRENCIES.map(([code, name]) => <option key={code} value={code}>{code} — {name}</option>)}</select></Field>
                    <Field label="Language" className="grow" hint="Read as $settings.locale."><select value={editor.draft.locale || ""} onChange={(e) => editor.set({ locale: e.target.value })}><option value="">Not set</option>{LOCALES.map(([code, name]) => <option key={code} value={code}>{code} — {name}</option>)}</select></Field>
                  </div>
                </div>
              </Card>
            )}
            {active === "app" && <AppDetails editor={editor} env={env} />}
            {active === "environment" && (
              <Environment env={env} path={editor.path} data={data} settings={editor.original} draftDirty={editor.count > 0} onImport={(incoming) => { editor.set(incoming); setActive("general"); }} onDelete={() => setConfirmDelete(true)} reload={editor.state.reload} />
            )}
          </div>
        )}
      </Loading>
      {confirmDelete && <DeleteEnvironment env={env} path={editor.path} onClose={() => setConfirmDelete(false)} onQueued={() => router.visit("/environments")} />}
    </Layout>
  );
}

/** How the app introduces itself. Mail templates read these as {{ app.name }} and the like, and replies go to the support address. */
function AppDetails({ editor, env }) {
  const app = editor.draft.app || {};
  const set = (values) => editor.set({ app: { ...app, ...values } });
  return (
    <Card icon="mail" title="App details" description="How your app introduces itself to the people it emails." actions={<SaveButtons editor={editor} />}>
      <div className="stack">
        <Field label="App name" hint="Available to every email template as {{ app.name }}."><input maxLength={80} value={app.name || ""} onChange={(e) => set({ name: e.target.value })} placeholder="Northwind" /></Field>
        <Field label="Support email" hint="Replies to your emails go here, and templates can show it as {{ app.support_email }}."><input type="email" maxLength={254} value={app.support_email || ""} onChange={(e) => set({ support_email: e.target.value })} placeholder="help@northwind.com" /></Field>
        <Field label="Postal address" optional hint="Many countries require one in marketing email. Templates read it as {{ app.address }}."><input maxLength={300} value={app.address || ""} onChange={(e) => set({ address: e.target.value })} placeholder="12 Marina Road, Lagos" /></Field>
        <Field label="Website" optional hint="Read as {{ app.website }}."><input type="url" maxLength={300} value={app.website || ""} onChange={(e) => set({ website: e.target.value })} placeholder="https://northwind.com" /></Field>
        <p className="hint">These fill in the values of your <Link href={envHref(env, "mail")} className="linklike">email templates</Link> automatically. Anything you pass in when sending still wins.</p>
      </div>
    </Card>
  );
}

function Environment({ env, path, data, settings, draftDirty, onImport, onDelete, reload }) {
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
  return <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 22 }}>
    <Section icon="networking" title="Environment" description="The default environment answers requests that name no environment.">
      <div className="spread flex items-center justify-between gap-3">
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">{data.is_default ? <Badge tone="green">default environment</Badge> : <Button disabled={busy} onClick={async () => { if (await run(() => patch(path, { is_default: true }), "Now the default")) reload(); }}>Make default</Button>}<span className="muted text-muted">Definitions version {data.version}</span></div>
        <Link className="link-more inline-flex items-center gap-0.5 text-ui-sm font-semibold text-muted [&:hover]:text-text" href={envHref(env, "releases")}>Releases <Icon name="chevronRight" size={14} /></Link>
      </div>
    </Section>
    <Section icon="upload" title="Export and import" description="Move settings between environments. Importing fills the form; nothing changes until you review it and press Save.">
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 10 }}>
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><Button size="sm" onClick={download}><Icon name="upload" />Download settings.json</Button></div>
        <Field label="Import settings" hint={draftDirty ? "You have unsaved changes; importing merges over them." : "Paste an exported settings.json. Keys not in the file are left alone."} error={error}>
          <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder='{"timezone": "Africa/Lagos", "currency": "NGN"}' spellCheck={false} />
        </Field>
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><Button size="sm" disabled={!text.trim()} onClick={load}>Load into form</Button></div>
      </div>
    </Section>
    {!data.is_default && <Section icon="trash" title="Danger zone" description="Deleting an environment removes its data, keys, secrets and users for good.">
      <Button variant="danger" disabled={data.settings?.deletion_pending} onClick={onDelete}>{data.settings?.deletion_pending ? "Deletion queued" : "Delete environment"}</Button>
    </Section>}
  </div>;
}

function DeleteEnvironment({ env, path, onClose, onQueued }) {
  const [value, setValue] = useState("");
  const [run, busy] = useAction();
  const ready = value === env;
  return <Modal title={`Delete ${env}?`} onClose={onClose} footer={<><Button onClick={onClose} disabled={busy}>Cancel</Button><Button variant="danger" disabled={!ready || busy} onClick={async () => { if (await run(() => del(path), "Deletion queued in the background")) onQueued(); }}>Permanently delete</Button></>}>
    <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] danger"><b>This cannot be undone.</b> The background purge permanently removes this environment’s resource records and tables, storage objects, API keys, secrets, users, sessions, MFA data, organizations, mail, request, flow, event and audit logs.</div>
    <p className="muted text-muted" style={{ margin: 0 }}>The environment is marked for deletion and will disappear shortly. A final installation-level receipt is retained for accountability.</p>
    <Field label={`Type ${env} to confirm`}><input autoFocus value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" /></Field>
  </Modal>;
}
