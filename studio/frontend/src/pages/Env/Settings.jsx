import { router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, IconButton, KeyValue, Loading, PageHead, Section, Switch, TagInput, useAction } from "../../components/ui";
import { PolicyPicker } from "../../components/definitions/PolicyPicker";
import { del, envPath, patch, useApi } from "../../lib/api";

// The realtime and CORS/docs fields Angula and the gateway actually read
// (services/angula/app/realtime.py ChannelConfig, routes/platform/data.py
// public_docs). Anything else under `settings` is open-ended data a flow
// might read as `$settings.<key>` — that stays a small key/value escape
// hatch rather than pretending to be a known option.
const KNOWN_KEYS = ["public_docs", "cors_origins", "realtime"];
const BLANK_RULE = { pattern: "", subscribe: null, publish: null, presence: true, history: 50 };

// "Default" in a PolicyPicker is represented as `null` so the control has
// something to show — but the backend only applies its own default when the
// key is *absent* (`rule.get("subscribe", "authenticated")`), not when it's
// present and null. Drop null/undefined keys before they go over the wire so
// "Default" really means "not set" rather than a literal null policy.
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

export default function Settings({ env }) {
  const path = envPath(env);
  const state = useApi(path);
  const policiesState = useApi(envPath(env, "/policies"));
  const [draft, setDraft] = useState({});
  const [run, busy] = useAction();
  const save = async (section) => {
    if (await run(() => patch(path, { [section]: omitNulls(draft[section]) }), "Saved")) { setDraft({ ...draft, [section]: undefined }); state.reload(); }
  };
  const policyNames = (policiesState.data?.data || []).map((p) => p.name);
  return (
    <Layout title="Settings">
      <PageHead title="Settings" description="Infrastructure (database, storage, mail) is configured once for the whole installation via .env, not per environment here." />
      <Loading state={state}>
        {(data) => (
          <SettingsForm
            settings={draft.settings ?? data.settings ?? {}}
            onChange={(settings) => setDraft({ ...draft, settings })}
            onSave={() => save("settings")}
            dirty={draft.settings !== undefined}
            busy={busy}
            policyNames={policyNames}
          >
            <Card title="Environment">
              <div className="spread">
                <div className="row">{data.is_default ? <Badge tone="green">default environment</Badge> : <Button onClick={async () => { if (await run(() => patch(path, { is_default: true }), "Now the default")) state.reload(); }}>Make default</Button>}<span className="muted">Definitions version {data.version}</span></div>
                {!data.is_default && <Button variant="danger" onClick={async () => { if (prompt(`Type ${env} to delete this environment`) === env && await run(() => del(path), "Environment deleted")) router.visit("/environments"); }}>Delete environment</Button>}
              </div>
            </Card>
          </SettingsForm>
        )}
      </Loading>
    </Layout>
  );
}

function SettingsForm({ settings, onChange, onSave, dirty, busy, policyNames, children }) {
  const [active, setActive] = useState("docs");
  const set = (patch) => onChange({ ...settings, ...patch });
  const realtime = settings.realtime || {};
  const setRealtime = (patch) => set({ realtime: { ...realtime, ...patch } });
  const rules = realtime.channels || [];
  const setRules = (channels) => setRealtime({ channels });
  const custom = Object.fromEntries(Object.entries(settings).filter(([k]) => !KNOWN_KEYS.includes(k)));
  const setCustom = (next) => onChange({ ...Object.fromEntries(KNOWN_KEYS.map((k) => [k, settings[k]]).filter(([, v]) => v !== undefined)), ...next });

  const sections = [
    ["docs", "API documentation", "Control public OpenAPI access"],
    ["cors", "CORS", "Configure browser origins"],
    ["realtime", "Realtime", "Set channel and policy rules"],
    ["custom", "Custom settings", "Add your own values"],
  ];
  const title = sections.find(([key]) => key === active)?.[1];
  return <div className="stack lg">
    <div style={{ display: "flex", gap: 18, alignItems: "flex-start" }}>
      <nav className="card" style={{ width: 230, flexShrink: 0, padding: 8 }} aria-label="Settings sections">
        {sections.map(([key, label, description]) => <button key={key} type="button" onClick={() => setActive(key)} style={{ width: "100%", textAlign: "left", border: 0, borderRadius: 8, padding: "11px 10px", background: active === key ? "var(--accent-soft)" : "transparent", color: "inherit", cursor: "pointer" }}><b style={{ display: "block", fontSize: 13 }}>{label}</b><span className="hint" style={{ fontSize: 11 }}>{description}</span></button>)}
      </nav>
      <Card title={title} actions={<Button variant="primary" size="sm" disabled={busy || !dirty} onClick={onSave}>Save changes</Button>} className="grow">
        {active === "docs" && <Switch checked={!!settings.public_docs} onChange={(v) => set({ public_docs: v })} label="Publish generated API docs" hint="Makes the OpenAPI reference public at /docs/v1. Operators can always see it from Studio either way." />}
        {active === "cors" && <Field label="Allowed browser origins" hint="Sites allowed to call the gateway with a publishable key from JavaScript. Leave empty to allow none."><TagInput value={settings.cors_origins || []} onChange={(v) => set({ cors_origins: v })} placeholder="https://app.example.com" /></Field>}
        {active === "custom" && <Field label="Custom values" hint="Extra keys flows and policies can read as $settings.<key>. They are not platform configuration."><KeyValue value={custom} onChange={setCustom} keyLabel="Key" valueLabel="Value" addLabel="Add setting" /></Field>}
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
      </Card>
    </div>
    {children}
  </div>;
}
