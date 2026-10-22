// Delivery: the SMTP providers this environment can send through, which one is
// the default, the sender addresses mail comes from, and a live connection
// test for each provider.
//
// Everything is stored on the environment (`infra.mail`). The default
// provider's settings sit at the top of that block, where the server reads
// them; the other providers and the senders are kept beside them. Passwords
// are encrypted secrets, referenced as `secret://NAME`, never stored in the
// block itself.

import { useEffect, useState } from "react";
import { Icon } from "../../../components/icons";
import { Badge, Button, Card, Field, Modal, Section, Segmented, Spinner, Switch, useAction, when } from "../../../components/ui";
import { envPath, patch, post, put } from "../../../lib/api";

export const PRESETS = [
  ["custom", "Custom SMTP", {}],
  ["resend", "Resend", { host: "smtp.resend.com", port: 465, username: "resend" }],
  ["sendgrid", "SendGrid", { host: "smtp.sendgrid.net", port: 587, username: "apikey" }],
  ["mailgun", "Mailgun", { host: "smtp.mailgun.org", port: 587 }],
  ["gmail", "Gmail", { host: "smtp.gmail.com", port: 587 }],
  ["ses", "Amazon SES", { host: "email-smtp.us-east-1.amazonaws.com", port: 587 }],
  ["postmark", "Postmark", { host: "smtp.postmarkapp.com", port: 587 }],
];
const LEGACY_SECRET = "MAIL_SMTP_PASSWORD";
const secretFor = (id) => `MAIL_SMTP_PASSWORD_${id.toUpperCase().replace(/[^A-Z0-9]/g, "_")}`;
const secretName = (ref) => (String(ref || "").startsWith("secret://") ? String(ref).slice(9) : null);
const presetOf = (host) => (PRESETS.find(([, , p]) => p.host && p.host === host) || ["custom"])[0];

/** The mail block as Studio edits it. An older block with one provider at the top becomes that provider. */
export function readMail(block) {
  const mail = block || {};
  let providers = Array.isArray(mail.providers) ? mail.providers : [];
  if (!providers.length && mail.host) {
    providers = [{ id: "primary", name: PRESETS.find(([k]) => k === presetOf(mail.host))?.[1] || "SMTP", preset: presetOf(mail.host), host: mail.host, port: mail.port || 587, username: mail.username || "", password: mail.password || "", use_ssl: mail.use_ssl, use_tls: mail.use_tls }];
  }
  const senders = Array.isArray(mail.senders) && mail.senders.length ? mail.senders : mail.from ? [{ name: "", email: mail.from, default: true }] : [];
  return { providers, defaultId: mail.default_provider || providers[0]?.id || null, senders, replyTo: mail.reply_to || "", suppress: Boolean(mail.suppress) };
}

/** Back to the stored block: the default provider and default sender at the top, the rest beside them. */
function writeMail({ providers, defaultId, senders, replyTo, suppress }) {
  const active = providers.find((p) => p.id === defaultId) || providers[0];
  const sender = senders.find((s) => s.default) || senders[0];
  if (!active) return senders.length ? { providers: [], senders, reply_to: replyTo || undefined, suppress: suppress || undefined } : null;
  return {
    host: active.host, port: Number(active.port) || 587, username: active.username || undefined, password: active.password || undefined,
    ...(active.use_ssl !== undefined ? { use_ssl: active.use_ssl } : {}), ...(active.use_tls !== undefined ? { use_tls: active.use_tls } : {}),
    from: sender ? (sender.name ? `${sender.name} <${sender.email}>` : sender.email) : undefined,
    reply_to: replyTo || undefined,
    suppress: suppress || undefined,
    providers: providers.map(({ last_test, ...p }) => ({ ...p, ...(last_test ? { last_test } : {}) })),
    default_provider: active.id,
    senders,
  };
}

export function Delivery({ env, environment, setup, onSaved }) {
  const base = envPath(env);
  const [state, setState] = useState(null);
  const [editing, setEditing] = useState(null);
  const [run, busy] = useAction();
  useEffect(() => { if (environment.data && !state) setState(readMail(environment.data.infra?.mail)); }, [environment.data]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!state) return <div className="empty"><Spinner /></div>;

  const persist = async (next, message) => {
    const ok = await run(() => patch(base, { infra: { mail: writeMail(next) } }), message);
    if (ok) { setState(next); environment.reload(); onSaved?.(); }
    return ok;
  };
  const test = async (provider) => {
    const result = await post(`${base}/mail/connection`, { host: provider.host, port: Number(provider.port) || 587, username: provider.username || null, password_secret: secretName(provider.password), use_ssl: provider.use_ssl, use_tls: provider.use_tls });
    const last_test = { ok: result.ok, at: new Date().toISOString(), detail: result.ok ? result.steps.join(" · ") : result.error };
    const next = { ...state, providers: state.providers.map((p) => (p.id === provider.id ? { ...p, last_test } : p)) };
    await persist(next, result.ok ? `Connected to ${provider.host}` : null);
    return result;
  };
  const sender = state.senders.find((s) => s.default) || state.senders[0];
  const configured = Boolean(setup?.configured) && !setup?.suppressed;

  return (
    <div className="stack lg">
      <div className={`alert ${configured ? "ok" : "warn"}`}>
        {configured
          ? <>Mail goes out through <b>{setup.settings.find((r) => r.setting === "host")?.value}</b> as <b>{setup.settings.find((r) => r.setting === "from")?.value}</b>.</>
          : setup?.configured ? <>Delivery is paused: messages are recorded as <i>suppressed</i> and not sent.</> : <>No provider is set up, so messages are recorded as <i>suppressed</i> and not delivered. Add one below.</>}
        {setup?.source === "variables" && <> These settings come from environment variables; saving a provider here takes over from them.</>}
      </div>

      <Card icon="send" flush title="Providers" description="The SMTP services this environment can send through. The default one sends everything." actions={<Button size="sm" variant="primary" onClick={() => setEditing({ isNew: true, id: Date.now().toString(36), name: "", preset: "custom", host: "", port: 587, username: "", password: "" })}><Icon name="plus" />Add provider</Button>}>
        {state.providers.length === 0 ? <div className="empty">No provider yet. Resend, SendGrid, Mailgun, Postmark, Amazon SES, Gmail or any SMTP server works.</div> : (
          <div className="mail-providers">
            {state.providers.map((p) => (
              <div key={p.id} className="mail-provider">
                <div className="mail-provider-main">
                  <b>{p.name || p.host}</b>{p.id === state.defaultId && <Badge tone="green">Default</Badge>}
                  <small className="mono">{p.host}:{p.port}{p.username ? ` · ${p.username}` : ""}</small>
                  {p.last_test && <small className={p.last_test.ok ? "text-ok" : "text-danger"}>{p.last_test.ok ? "Connected" : "Failed"} {when(p.last_test.at)}: {p.last_test.detail}</small>}
                </div>
                <div className="row">
                  <TestButton onTest={() => test(p)} />
                  {p.id !== state.defaultId && <Button size="sm" disabled={busy} onClick={() => persist({ ...state, defaultId: p.id }, `${p.name || p.host} is now the default`)}>Make default</Button>}
                  <Button size="sm" onClick={() => setEditing({ ...p, password: "" , has_password: Boolean(p.password) })}>Edit</Button>
                  <Button size="sm" variant="danger" disabled={busy} onClick={() => confirm(`Remove ${p.name || p.host}?`) && persist({ ...state, providers: state.providers.filter((x) => x.id !== p.id), defaultId: state.defaultId === p.id ? state.providers.find((x) => x.id !== p.id)?.id || null : state.defaultId }, "Provider removed")}>Remove</Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Senders state={state} onSave={(senders, replyTo) => persist({ ...state, senders, replyTo }, "Senders saved")} busy={busy} />

      <Section icon="pulse" title="Delivery" description="Keep the settings but stop sending, for example while testing.">
        <Switch checked={state.suppress} onChange={(v) => persist({ ...state, suppress: v }, v ? "Delivery paused" : "Delivery resumed")} label="Pause delivery" hint="Messages are recorded as suppressed and shown under Activity." />
      </Section>

      {sender && <p className="hint">Mail without its own sender comes from <b>{sender.name ? `${sender.name} <${sender.email}>` : sender.email}</b>. The sender's domain must be verified with your provider (SPF and DKIM), or mail lands in spam.</p>}

      {editing && <ProviderModal base={base} provider={editing} onClose={() => setEditing(null)} onSave={async (provider) => {
        const providers = editing.isNew ? [...state.providers, provider] : state.providers.map((p) => (p.id === provider.id ? provider : p));
        if (await persist({ ...state, providers, defaultId: state.defaultId || provider.id }, "Provider saved")) setEditing(null);
      }} />}
    </div>
  );
}

function TestButton({ onTest }) {
  const [busy, setBusy] = useState(false);
  return <Button size="sm" disabled={busy} onClick={async () => { setBusy(true); try { await onTest(); } finally { setBusy(false); } }}>{busy ? "Testing…" : "Test connection"}</Button>;
}

function ProviderModal({ base, provider, onClose, onSave }) {
  const [form, setForm] = useState(provider);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const choose = (key) => {
    const [, label, preset] = PRESETS.find(([k]) => k === key);
    setForm((f) => ({ ...f, preset: key, name: f.name || (key === "custom" ? "" : label), ...preset, username: preset.username ?? f.username }));
  };
  const test = async () => {
    setBusy(true);
    try {
      setResult(await post(`${base}/mail/connection`, { host: form.host, port: Number(form.port) || 587, username: form.username || null, password: form.password || null, password_secret: form.password ? null : secretName(provider.password) }));
    } catch (e) { setResult({ ok: false, error: e.message }); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true);
    try {
      let password = provider.password || "";
      if (form.password) {
        const name = provider.password ? secretName(provider.password) || secretFor(form.id) : secretFor(form.id);
        await put(`${base}/secrets/${name}`, { value: form.password, description: `SMTP password for ${form.name || form.host}` });
        password = `secret://${name}`;
      }
      const { isNew, has_password, ...rest } = form;
      await onSave({ ...rest, port: Number(form.port) || 587, password, last_test: result ? { ok: result.ok, at: new Date().toISOString(), detail: result.ok ? result.steps.join(" · ") : result.error } : provider.last_test });
    } finally { setBusy(false); }
  };
  return (
    <Modal title={provider.isNew ? "Add a provider" : `Edit ${provider.name || provider.host}`} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button disabled={busy || !form.host} onClick={test}>{busy ? "Working…" : "Test connection"}</Button><Button variant="primary" disabled={busy || !form.host} onClick={save}>Save</Button></>}>
      <div className="stack">
        <Field label="Service"><Segmented value={form.preset || "custom"} onChange={choose} options={PRESETS.map(([k, l]) => [k, l])} /></Field>
        <Field label="Name"><input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Transactional" /></Field>
        <div className="row top" style={{ gap: 12 }}>
          <Field label="Host" className="grow"><input value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="smtp.example.com" /></Field>
          <Field label="Port" hint="465 is SSL, 587 STARTTLS."><input type="number" value={form.port} onChange={(e) => set("port", e.target.value)} style={{ width: 96 }} /></Field>
        </div>
        <Field label="Username"><input value={form.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" /></Field>
        <Field label="Password or API key" hint={form.has_password || provider.password ? "Stored encrypted. Leave empty to keep it." : "Stored as an encrypted secret and never shown again."}>
          <input type="password" value={form.password} onChange={(e) => set("password", e.target.value)} placeholder={form.has_password || provider.password ? "••••••••" : ""} autoComplete="new-password" />
        </Field>
        {result && <div className={`alert ${result.ok ? "ok" : "error"}`}>{result.ok ? <>Connected: {result.steps.join(" · ")}</> : result.error}</div>}
      </div>
    </Modal>
  );
}

function Senders({ state, onSave, busy }) {
  const [senders, setSenders] = useState(state.senders);
  const [replyTo, setReplyTo] = useState(state.replyTo);
  const dirty = JSON.stringify(senders) !== JSON.stringify(state.senders) || replyTo !== state.replyTo;
  const update = (i, patch) => setSenders((list) => list.map((s, j) => (j === i ? { ...s, ...patch } : patch.default ? { ...s, default: false } : s)));
  return (
    <Card icon="mail" title="Senders" description="The addresses mail can come from. Templates and the composer pick one; everything else uses the default." actions={dirty && <Button size="sm" variant="primary" disabled={busy} onClick={() => onSave(senders.map((s, i) => ({ ...s, default: s.default || (!senders.some((x) => x.default) && i === 0) })), replyTo)}>Save senders</Button>}>
      <div className="stack sm">
        {senders.map((s, i) => (
          <div key={i} className="mail-sender">
            <input value={s.name} placeholder="Name, e.g. Northwind" onChange={(e) => update(i, { name: e.target.value })} />
            <input value={s.email} placeholder="orders@yourdomain.com" onChange={(e) => update(i, { email: e.target.value.trim() })} />
            <label className="check"><input type="radio" name="default-sender" checked={Boolean(s.default)} onChange={() => update(i, { default: true })} />Default</label>
            <button type="button" className="mail-icon-btn danger" onClick={() => setSenders((l) => l.filter((_, j) => j !== i))} title="Remove"><Icon name="trash" size={13} /></button>
          </div>
        ))}
        <div><Button size="sm" onClick={() => setSenders((l) => [...l, { name: "", email: "", default: l.length === 0 }])}><Icon name="plus" />Add a sender</Button></div>
        <Field label="Reply to" optional hint="Where replies go when a message does not say."><input value={replyTo} onChange={(e) => setReplyTo(e.target.value)} placeholder="support@yourdomain.com" /></Field>
      </div>
    </Card>
  );
}
