import { useEffect, useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Stat, StatStrip } from "./observability/kit";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, EmptyState, Field, JsonInput, Loading, Modal, PageHead, Segmented, Sheet, Spinner, Status, Switch, Table, Tile, copyToClipboard, useAction, useToast, when } from "../../components/ui";
import { del, envPath, patch, post, put, useApi } from "../../lib/api";

const PROVIDERS = [
  ["custom", "Custom", {}],
  ["resend", "Resend", { host: "smtp.resend.com", port: 465, username: "resend" }],
  ["sendgrid", "SendGrid", { host: "smtp.sendgrid.net", port: 587, username: "apikey" }],
  ["mailgun", "Mailgun", { host: "smtp.mailgun.org", port: 587, username: "" }],
  ["gmail", "Gmail", { host: "smtp.gmail.com", port: 587, username: "" }],
  ["ses", "Amazon SES", { host: "email-smtp.us-east-1.amazonaws.com", port: 587, username: "" }],
  ["postmark", "Postmark", { host: "smtp.postmarkapp.com", port: 587, username: "" }],
];
const PASSWORD_SECRET = "MAIL_SMTP_PASSWORD";
const STATUSES = [["all", "All"], ["sent", "Sent"], ["suppressed", "Suppressed"], ["failed", "Failed"]];

export default function Mail({ env }) {
  const base = envPath(env);
  const log = useApi(`${base}/mail/log`, { params: { limit: 200 }, interval: 10000 });
  const environment = useApi(base);
  const [filter, setFilter] = useState("all");
  const [config, setConfig] = useState(false);
  const [test, setTest] = useState(false);
  const [open, setOpen] = useState(null);

  const setup = useApi(`${base}/mail/config`);
  const settingOf = (name) => (setup.data?.settings || []).find((row) => row.setting === name)?.value;
  const configured = Boolean(setup.data?.configured) && !setup.data?.suppressed;
  const rows = log.data?.data || [];
  const shown = filter === "all" ? rows : rows.filter((r) => r.status === filter);

  return (
    <Layout title="Mail">
      <PageHead
        title="Mail"
        description="Everything this environment sent, or would have sent. Flows, functions and the sign-in service send mail through the delivery settings and templates configured here."
        actions={<>
          <Button onClick={() => setTest(true)}><Icon name="send" />Send test</Button>
          <Button variant="primary" onClick={() => setConfig(true)}><Icon name="settings" />Mail setup</Button>
        </>}
      />

      {!setup.loading && setup.data && (
        configured ? (
          <div className="alert ok" style={{ marginBottom: 16 }}>
            Delivering through <b>{settingOf("host")}:{settingOf("port")}</b> as <b>{settingOf("from")}</b>.
          </div>
        ) : (
          <div className="alert warn" style={{ marginBottom: 16 }}>
            <b>{setup.data.configured ? "Delivery is paused, so nothing is sent." : "No mail server is configured, so nothing is delivered."}</b> Messages are recorded below as <i>suppressed</i> so you can see what would have gone out.{" "}
            <a href="#" onClick={(e) => { e.preventDefault(); setConfig(true); }}>See how mail is set up</a>
          </div>
        )
      )}


      <Card flush title="Sent mail" actions={<Segmented value={filter} onChange={setFilter} options={STATUSES} />}>
        <Loading state={log} empty="Nothing has been sent from this environment yet.">
          {() => (
            <Table
              rows={shown}
              empty={`No ${filter} messages.`}
              onRowClick={setOpen}
              columns={[
                { label: "When", render: (r) => when(r.created_at) },
                { label: "To", render: (r) => <span title={(r.to || []).join(", ")}>{(r.to || [])[0]}{(r.to || []).length > 1 ? ` +${r.to.length - 1}` : ""}</span> },
                { label: "Subject", key: "subject" },
                { label: "Template", render: (r) => (r.template ? <Badge tone="lavender">{r.template}</Badge> : <span className="faint">—</span>) },
                { label: "Status", render: (r) => <Status value={r.status} /> },
                { label: "From", render: (r) => <span className="muted">{r.source}</span> },
              ]}
            />
          )}
        </Loading>
      </Card>

      {open && <MessageSheet message={open} onClose={() => setOpen(null)} />}
      {test && <TestSheet base={base} onClose={() => setTest(false)} onSent={() => { setTest(false); log.reload(); }} />}
      {config && <ConfigSheet base={base} environment={environment} onSaved={() => { setup.reload(); environment.reload(); }} onClose={() => setConfig(false)} />}
    </Layout>
  );
}

function MessageSheet({ message, onClose }) {
  return (
    <Modal title={message.subject || "(no subject)"} onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      <div className="stack">
        <div className="row wrap"><Status value={message.status} />{message.template && <Badge tone="lavender">{message.template}</Badge>}<span className="muted">{when(message.created_at)}</span></div>
        <Field label="To"><div>{(message.to || []).join(", ")}</div></Field>
        <Field label="Sent from"><div>{message.source}</div></Field>
        {message.message_id && <Field label="Message id"><code>{message.message_id}</code></Field>}
        {message.error && <div className="alert error"><b>Delivery failed</b><br />{message.error}</div>}
        {message.status === "suppressed" && <div className="alert info">No mail server is configured, so this message was recorded and not delivered.</div>}
      </div>
    </Modal>
  );
}

function TestSheet({ base, onClose, onSent }) {
  const templates = useApi(`${base}/mail-templates`);
  const [to, setTo] = useState("");
  const [template, setTemplate] = useState("");
  const [subject, setSubject] = useState("Pawabase mail test");
  const [text, setText] = useState("If you can read this, outgoing mail works.");
  const [data, setData] = useState({});
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  const send = async () => {
    const answer = await run(() => post(`${base}/mail/test`, { to: to.split(/[\s,;]+/).filter(Boolean), subject: template ? "" : subject, text: template ? undefined : text, template: template || undefined, data }));
    if (answer) setResult(answer === true ? {} : answer);
  };
  return (
    <Modal title="Send a test email" onClose={onClose} footer={<><Button onClick={onClose}>Close</Button><Button variant="primary" disabled={busy || !to.trim()} onClick={send}>{busy ? "Sending…" : "Send"}</Button></>}>
      <div className="stack">
        <Field label="To" hint="One or more addresses, separated by commas."><input value={to} onChange={(e) => setTo(e.target.value)} placeholder="you@example.com" autoFocus /></Field>
        <Field label="Template" optional hint="Leave empty to send the plain message below.">
          <select value={template} onChange={(e) => setTemplate(e.target.value)}>
            <option value="">No template: a plain message</option>
            {(templates.data?.data || []).map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
          </select>
        </Field>
        {template ? (
          <Field label="Template data" hint="The values the template reads, as JSON."><JsonInput value={data} onChange={setData} rows={8} /></Field>
        ) : (
          <>
            <Field label="Subject"><input value={subject} onChange={(e) => setSubject(e.target.value)} /></Field>
            <Field label="Message"><textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} /></Field>
          </>
        )}
        {result && (result.suppressed
          ? <div className="alert warn">Recorded but not delivered: no mail server is configured.</div>
          : <div className="alert ok">Sent to {(result.to || []).join(", ")}.</div>)}
        {result && <Button size="sm" onClick={onSent}>Done</Button>}
      </div>
    </Modal>
  );
}

function ConfigSheet({ base, environment, onSaved, onClose }) {
  const [tab, setTab] = useState("delivery");
  return (
    <Sheet
      title="Mail"
      subtitle="How this environment delivers mail, and what it says"
      icon="mail"
      tone="rose"
      tabs={[{ value: "delivery", label: "Delivery" }, { value: "templates", label: "Templates" }]}
      tab={tab}
      onTab={setTab}
      onClose={onClose}
      footer={<Button onClick={onClose}>Done</Button>}
    >
      {tab === "delivery" ? <Delivery base={base} environment={environment} onSaved={onSaved} /> : <Templates base={base} />}
    </Sheet>
  );
}

function Delivery({ base, environment, onSaved }) {
  const saved = environment.data?.infra?.mail || {};
  const [form, setForm] = useState(null);
  const [provider, setProvider] = useState("custom");
  const [run, busy] = useAction();
  useEffect(() => {
    if (!environment.data || form) return;
    const referencesSecret = String(saved.password || "").startsWith("secret://");
    setForm({
      host: saved.host || "", port: saved.port || 587, username: saved.username || "", password: "", has_password: referencesSecret || Boolean(saved.password),
      from: saved.from || "", reply_to: saved.reply_to || "", suppress: Boolean(saved.suppress),
    });
    setProvider((PROVIDERS.find(([, , preset]) => preset.host && preset.host === saved.host) || ["custom"])[0]);
  }, [environment.data]);
  if (!form) return <Loading state={environment}>{() => <div className="empty"><Spinner /></div>}</Loading>;
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const choose = (key) => {
    setProvider(key);
    const preset = PROVIDERS.find(([k]) => k === key)[2];
    setForm((f) => ({ ...f, ...preset, username: preset.username !== undefined && preset.username !== "" ? preset.username : f.username }));
  };

  const save = async () => {
    const ok = await run(async () => {
      let password = saved.password;
      if (form.password) {
        // The password never goes into the configuration itself: it is stored as an encrypted secret and referenced.
        await put(`${base}/secrets/${PASSWORD_SECRET}`, { value: form.password, description: "SMTP password for outgoing mail" });
        password = `secret://${PASSWORD_SECRET}`;
      }
      const block = form.host.trim() ? {
        host: form.host.trim(), port: Number(form.port) || 587, username: form.username.trim() || undefined, password: password || undefined,
        from: form.from.trim() || undefined, reply_to: form.reply_to.trim() || undefined, suppress: form.suppress || undefined,
      } : null;
      await patch(base, { infra: { mail: block } });
    }, form.host.trim() ? "Mail settings saved" : "Mail settings cleared: nothing will be delivered");
    if (ok) { setForm((f) => ({ ...f, password: "", has_password: true })); environment.reload(); onSaved?.(); }
  };

  return (
    <div className="stack">
      <section className="form-section">
        <div className="form-section-head"><div><h3>Mail server</h3><p>Any SMTP service. Without a host, mail is recorded as suppressed and not delivered.</p></div></div>
        <Field label="Provider"><Segmented value={provider} onChange={choose} options={PROVIDERS.map(([k, label]) => [k, label])} /></Field>
        <div className="row top" style={{ gap: 12 }}>
          <Field label="Host" className="grow"><input value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="smtp.example.com" /></Field>
          <Field label="Port" hint="465 uses SSL, 587 uses STARTTLS."><input type="number" value={form.port} onChange={(e) => set("port", e.target.value)} style={{ width: 96 }} /></Field>
        </div>
        <Field label="Username"><input value={form.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" /></Field>
        <Field label="Password or API key" hint={form.has_password ? "A password is stored (encrypted). Leave empty to keep it." : "Stored as an encrypted secret, never shown again."}>
          <input type="password" value={form.password} onChange={(e) => set("password", e.target.value)} placeholder={form.has_password ? "••••••••" : ""} autoComplete="new-password" />
        </Field>
      </section>
      <section className="form-section">
        <div className="form-section-head"><div><h3>Sender</h3><p>The sender's domain must be verified with your provider (SPF and DKIM), or mail lands in spam.</p></div></div>
        <Field label="From"><input value={form.from} onChange={(e) => set("from", e.target.value)} placeholder="Sell4me <orders@yourdomain.com>" /></Field>
        <Field label="Reply to" optional><input value={form.reply_to} onChange={(e) => set("reply_to", e.target.value)} placeholder="support@yourdomain.com" /></Field>
        <Switch checked={form.suppress} onChange={(v) => set("suppress", v)} label="Pause delivery" hint="Keep the settings but record messages as suppressed (useful while testing)." />
      </section>
      <div><Button variant="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save mail settings"}</Button></div>
    </div>
  );
}

function Templates({ base }) {
  const templates = useApi(`${base}/mail-templates`);
  const [editing, setEditing] = useState(null);
  const rows = templates.data?.data || [];
  return (
    <div className="stack">
      <div className="spread">
        <p className="muted" style={{ margin: 0, maxWidth: 420 }}>Written in Jinja. Flows and functions send a template by name and hand it data, for example <code>{"{{ order.number }}"}</code>.</p>
        <Button variant="primary" size="sm" onClick={() => setEditing({ isNew: true, name: "", description: "", subject: "", html: "", text: "" })}><Icon name="plus" />New template</Button>
      </div>
      <Loading state={templates}>
        {() => rows.length === 0
          ? <EmptyState icon="mail" tone="rose" title="No templates yet">Create one, then send it from a flow with the Send email block.</EmptyState>
          : <Table rows={rows} onRowClick={(row) => setEditing({ ...row })} columns={[{ label: "Name", render: (r) => <b>{r.name}</b> }, { label: "Subject", render: (r) => <span className="muted">{r.subject}</span> }]} />}
      </Loading>
      {editing && <TemplateEditor base={base} template={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); templates.reload(); }} />}
    </div>
  );
}

function TemplateEditor({ base, template, onClose, onSaved }) {
  const [data, setData] = useState(template);
  const [view, setView] = useState("html");
  const [run, busy] = useAction();
  const set = (key, value) => setData((d) => ({ ...d, [key]: value }));
  const body = { name: data.name, description: data.description || "", subject: data.subject || "", html: data.html || "", text: data.text || "" };
  const save = async () => {
    const ok = await run(() => (template.isNew ? post(`${base}/mail-templates`, body) : put(`${base}/mail-templates/${template.name}`, body)), "Template saved");
    if (ok) onSaved();
  };
  const remove = async () => {
    if (!confirm(`Delete the template ${template.name}? Flows that send it will fail until it exists again.`)) return;
    if (await run(() => del(`${base}/mail-templates/${template.name}`), "Template deleted")) onSaved();
  };
  return (
    <Modal
      title={template.isNew ? "New template" : template.name}
      onClose={onClose}
      footer={<>
        {!template.isNew && <Button variant="danger" disabled={busy} onClick={remove} style={{ marginRight: "auto" }}>Delete</Button>}
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={busy || !data.name.trim()} onClick={save}>{busy ? "Saving…" : "Save"}</Button>
      </>}
    >
      <div className="stack">
        <Field label="Name" hint="What a flow names to send it."><input value={data.name} disabled={!template.isNew} onChange={(e) => set("name", e.target.value)} placeholder="order_receipt" /></Field>
        <Field label="Description" optional><input value={data.description || ""} onChange={(e) => set("description", e.target.value)} /></Field>
        <Field label="Subject"><input value={data.subject || ""} onChange={(e) => set("subject", e.target.value)} placeholder="Your order #{{ order.number }}" /></Field>
        <div className="spread"><b>Body</b><Segmented value={view} onChange={setView} options={[["html", "HTML"], ["text", "Plain text"], ["preview", "Preview"]]} /></div>
        {view === "html" && <textarea className="mono" rows={16} value={data.html || ""} onChange={(e) => set("html", e.target.value)} placeholder="<p>Hi {{ name }},</p>" />}
        {view === "text" && <textarea className="mono" rows={16} value={data.text || ""} onChange={(e) => set("text", e.target.value)} placeholder="Hi {{ name }}," />}
        {view === "preview" && (
          <>
            <div className="hint">The HTML as written. <code>{"{{ values }}"}</code> and <code>{"{% logic %}"}</code> appear as typed here; send a test to see them filled in.</div>
            <iframe title="Template preview" sandbox="" srcDoc={data.html || "<p style='font-family:sans-serif;color:#888'>No HTML body.</p>"} style={{ width: "100%", height: 420, border: "1px solid var(--line)", borderRadius: 12, background: "#fff" }} />
          </>
        )}
      </div>
    </Modal>
  );
}
