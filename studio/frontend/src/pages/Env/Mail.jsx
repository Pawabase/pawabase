import { useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, EmptyState, Field, JsonInput, Loading, Modal, PageHead, Segmented, Sheet, Spinner, Status, Table, Tile, copyToClipboard, useAction, useToast, when } from "../../components/ui";
import { del, envPath, patch, post, put, useApi } from "../../lib/api";

const PROVIDERS = [
  ["custom", "Custom", {}],
  ["resend", "Resend", { host: "smtp.resend.com", port: 465, username: "resend" }],
  ["sendgrid", "SendGrid", { host: "smtp.sendgrid.net", port: 587, username: "apikey" }],
  ["mailgun", "Mailgun", { host: "smtp.mailgun.org", port: 587, username: "" }],
  ["gmail", "Gmail", { host: "smtp.gmail.com", port: 587, username: "" }],
];
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
  const counts = useMemo(() => rows.reduce((n, r) => ({ ...n, [r.status]: (n[r.status] || 0) + 1 }), {}), [rows]);
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

      <div className="grid" style={{ marginBottom: 16 }}>
        <Tile tone="mint" icon="check" value={counts.sent || 0} label="Sent" i={0} />
        <Tile tone="butter" icon="clock" value={counts.suppressed || 0} label="Suppressed" i={1} />
        <Tile tone="rose" icon="x" value={counts.failed || 0} label="Failed" i={2} />
        <Tile tone="lavender" icon="mail" value={rows.length} label="Recent messages" i={3} />
      </div>

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
      {config && <ConfigSheet base={base} environment={environment} setup={setup} onClose={() => setConfig(false)} />}
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

function ConfigSheet({ base, environment, setup, onClose }) {
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
      {tab === "delivery" ? <Delivery base={base} environment={environment} setup={setup} /> : <Templates base={base} />}
    </Sheet>
  );
}

const SOURCES = { environment: ["this environment", "green"], deployment: ["every environment", "blue"], default: ["default", ""] };

/** Mail is configured by environment variables, not here: this shows what is in effect and how to change it. */
function Delivery({ base, environment, setup }) {
  const toast = useToast();
  const [run, busy] = useAction();
  const [provider, setProvider] = useState("custom");
  const [scope, setScope] = useState("environment");
  const data = setup.data;
  if (!data) return <Loading state={setup}>{() => <div className="empty"><Spinner /></div>}</Loading>;

  const preset = PROVIDERS.find(([key]) => key === provider)[2];
  const prefix = scope === "environment" ? data.environment_prefix : "PAWABASE_MAIL_";
  const lines = [
    `${prefix}HOST=${preset.host || "smtp.example.com"}`,
    `${prefix}PORT=${preset.port || 587}`,
    `${prefix}USERNAME=${preset.username || "your-username"}`,
    `${prefix}PASSWORD=your-password-or-api-key`,
    `${prefix}FROM=Your App <no-reply@yourdomain.com>`,
  ].join("\n");
  const clearStored = async () => {
    if (await run(() => patch(base, { infra: { mail: null } }), "Stored mail settings removed")) environment.reload();
  };
  const show = (row) => {
    if (row.setting === "password") return row.value ? "set" : "";
    if (typeof row.value === "boolean") return row.value ? "yes" : "no";
    return String(row.value ?? "");
  };

  return (
    <div className="stack">
      {data.legacy && (
        <div className="alert warn">
          <b>Mail settings stored on this environment are no longer used.</b> Older versions kept the mail server here; it now comes from environment variables. Print the old values as variables with{" "}
          <code>python -m app.export_config --env {environment.data?.name || "…"}</code>, set them on the deployment, then remove the stored copy.
          <div style={{ marginTop: 8 }}><Button size="sm" disabled={busy} onClick={clearStored}>Remove stored settings</Button></div>
        </div>
      )}
      <section className="form-section">
        <div className="form-section-head"><div><h3>What is in effect</h3><p>{data.configured ? (data.suppressed ? "A server is configured but delivery is paused." : "Mail is delivered through this server.") : "No mail server is configured: mail is recorded, not sent."}</p></div></div>
        <Table
          rows={data.settings.map((row) => ({ ...row, id: row.setting }))}
          columns={[
            { label: "Setting", render: (row) => <code>{row.variable}</code> },
            { label: "Value", render: (row) => (show(row) ? <span>{show(row)}</span> : <span className="faint">not set</span>) },
            { label: "From", render: (row) => <Badge tone={SOURCES[row.source][1]}>{SOURCES[row.source][0]}</Badge> },
          ]}
        />
        <p className="muted" style={{ margin: "8px 0 0" }}>
          An environment's own <code>{data.environment_prefix}*</code> variable wins over the deployment-wide <code>PAWABASE_MAIL_*</code> one. The password is never shown.
        </p>
      </section>
      <section className="form-section">
        <div className="form-section-head"><div><h3>Set it up</h3><p>Add these to the deployment's environment (its <code>.env</code> or its host's variables) and restart it.</p></div></div>
        <Field label="Provider"><Segmented value={provider} onChange={setProvider} options={PROVIDERS.map(([k, label]) => [k, label])} /></Field>
        <Field label="For"><Segmented value={scope} onChange={setScope} options={[["environment", "This environment only"], ["deployment", "Every environment"]]} /></Field>
        <pre className="code-block">{lines}</pre>
        <div><Button size="sm" onClick={async () => toast((await copyToClipboard(lines)) ? "Copied" : "Could not copy", "ok")}><Icon name="copy" />Copy</Button></div>
        <p className="muted" style={{ margin: "8px 0 0" }}>
          Also available: <code>{prefix}REPLY_TO</code>, <code>{prefix}USE_SSL</code>, <code>{prefix}USE_TLS</code> and <code>{prefix}SUPPRESS=true</code> to keep the settings but pause delivery.
          A password can name one of this environment's secrets (<code>secret://NAME</code>) instead of holding the value.
        </p>
      </section>
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
