// Who may publish and subscribe on this environment's realtime channels, and the rules for particular channels.

import { Button, Card, Field, IconButton, Loading, Section, Switch } from "../ui";
import { Icon } from "../icons";
import { PolicyPicker } from "../definitions/PolicyPicker";
import { envPath, useApi } from "../../lib/api";
import { SaveButtons, useEnvSettings } from "../envSettings";

const BLANK_RULE = { pattern: "", subscribe: null, publish: null, presence: true, history: 50 };

export default function RealtimeSettings({ env }) {
  const editor = useEnvSettings(env);
  const policies = useApi(envPath(env, "/policies"));
  const names = (policies.data?.data || []).map((p) => p.name);
  const realtime = editor.draft.realtime || {};
  const setRealtime = (values) => editor.set({ realtime: { ...realtime, ...values } });
  const rules = realtime.channels || [];
  const setRules = (channels) => setRealtime({ channels });
  const change = (i, values) => setRules(rules.map((r, j) => (j === i ? { ...r, ...values } : r)));
  return (
    <Loading state={editor.state}>
      {() => !editor.ready ? null : (
        <Card icon="realtime" title="Channel access" description="Who may subscribe and publish. Applies to new connections within about 30 seconds." actions={<SaveButtons editor={editor} />}>
          <div className="stack">
            <Switch checked={realtime.allow_client_publish !== false} onChange={(v) => setRealtime({ allow_client_publish: v })} label="Apps may publish" hint="Off: only your servers and flows publish; apps can still listen." />
            <Field label="Default rule" hint="Used by any channel that matches none of the rules below."><PolicyPicker value={realtime.default_policy ?? null} onChange={(v) => setRealtime({ default_policy: v })} policies={names} nullLabel="Default (signed-in users)" /></Field>
            <Section icon="layers" title="Rules for particular channels" description="Matched top to bottom. A pattern can use * as a wildcard, and {{ auth.org }} for the user's organization.">
              {rules.length === 0 && <p className="hint">No rules yet: every channel uses the default rule.</p>}
              <div className="stack">
                {rules.map((rule, i) => (
                  <div key={i} className="list-card">
                    <div className="row" style={{ alignItems: "flex-end" }}>
                      <Field label="Channel pattern" className="grow"><input value={rule.pattern || ""} onChange={(e) => change(i, { pattern: e.target.value })} placeholder="org:{{ auth.org }}" /></Field>
                      <IconButton icon="trash" label="Remove rule" onClick={() => setRules(rules.filter((_, j) => j !== i))} />
                    </div>
                    <div className="grid two">
                      <Field label="Who can listen"><PolicyPicker value={rule.subscribe ?? null} onChange={(v) => change(i, { subscribe: v })} policies={names} nullLabel="Default rule" /></Field>
                      <Field label="Who can publish"><PolicyPicker value={rule.publish ?? null} onChange={(v) => change(i, { publish: v })} policies={names} nullLabel="Default rule" /></Field>
                    </div>
                    <div className="row top">
                      <Switch checked={rule.presence !== false} onChange={(v) => change(i, { presence: v })} label="Show who is here" />
                      <Field label="Messages kept for latecomers" className="grow"><input type="number" min={0} max={1000} value={rule.history ?? 50} onChange={(e) => change(i, { history: Number(e.target.value) || 0 })} /></Field>
                    </div>
                  </div>
                ))}
              </div>
              <div><Button size="sm" onClick={() => setRules([...rules, { ...BLANK_RULE }])}><Icon name="plus" />Add a rule</Button></div>
            </Section>
          </div>
        </Card>
      )}
    </Loading>
  );
}
