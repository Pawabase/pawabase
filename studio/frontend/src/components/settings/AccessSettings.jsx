// Who may call this environment's API from where, and the switch that puts it into maintenance.

import { Link } from "@inertiajs/react";
import { Card, Field, Loading, Switch, TagInput } from "../ui";
import { envHref } from "../Layout";
import { SaveButtons, useEnvSettings } from "../envSettings";

const BLANK_MAINTENANCE = { enabled: false, message: "", retry_after: 300, allow_secret_keys: true };

export default function AccessSettings({ env }) {
  const editor = useEnvSettings(env);
  const { draft, set } = editor;
  const maintenance = { ...BLANK_MAINTENANCE, ...(draft.maintenance || {}) };
  const setMaintenance = (values) => set({ maintenance: { ...maintenance, ...values } });
  return (
    <Loading state={editor.state}>
      {() => !editor.ready ? null : (
        <div className="stack lg">
          <Card icon="keys" title="Who can call your API" description="Applies within about 30 seconds." actions={<SaveButtons editor={editor} />}>
            <div className="stack">
              <Switch checked={!!draft.public_docs} onChange={(v) => set({ public_docs: v })} label="Publish the API reference" hint="Anyone can read your generated API documentation at /docs/v1. You can always see it in Studio." />
              <Field label="Websites allowed to call it" hint="Sites that may use a publishable key from JavaScript, like https://app.example.com. Leave empty to allow any site."><TagInput value={draft.cors_origins || []} onChange={(v) => set({ cors_origins: v })} placeholder="https://app.example.com" /></Field>
              <Field label="Servers allowed to use secret keys" hint="When set, secret keys only work from these addresses or ranges. Publishable keys are not affected. Leave empty to allow any address."><TagInput value={draft.ip_allowlist || []} onChange={(v) => set({ ip_allowlist: v })} placeholder="203.0.113.7 or 10.0.0.0/8" /></Field>
              {(draft.ip_allowlist || []).length > 0 && <div className="alert warn">Make sure your own servers are on the list before saving. Secret-key requests from anywhere else get 403 until you change it. Studio is not affected.</div>}
              <p className="hint">Limits for one key (expiry, routes, addresses) are set on <Link href={envHref(env, "keys")} className="linklike">API keys</Link>.</p>
            </div>
          </Card>
          <Card icon="clock" title="Maintenance mode" description="Tell your users you are working on things, without taking anything down.">
            <div className="stack">
              <Switch checked={maintenance.enabled} onChange={(v) => setMaintenance({ enabled: v })} label="Maintenance mode" hint="Your API answers “try again shortly” (503) instead of reaching your data." />
              {maintenance.enabled && <div className="alert warn">Maintenance mode is on. Your users cannot reach your API.</div>}
              <Field label="Message" hint="What your users' apps receive."><input maxLength={300} value={maintenance.message} onChange={(e) => setMaintenance({ message: e.target.value })} placeholder="We are upgrading and will be back shortly." /></Field>
              <Field label="Ask them to retry after (seconds)" hint="Well-behaved apps wait this long before trying again."><input type="number" min={1} max={86400} value={maintenance.retry_after} onChange={(e) => setMaintenance({ retry_after: Math.min(86400, Math.max(1, Number(e.target.value) || 300)) })} /></Field>
              <Switch checked={maintenance.allow_secret_keys} onChange={(v) => setMaintenance({ allow_secret_keys: v })} label="Keep your servers working" hint="Requests with a secret key still go through, so your scripts and back office keep running." />
            </div>
          </Card>
        </div>
      )}
    </Loading>
  );
}
