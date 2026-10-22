// Sent mail: every message with its status, searchable and filterable, and one
// message in full — what it looked like, its delivery timeline, the provider's
// answer — with retry, cancel and send-a-copy.

import { useEffect, useState } from "react";
import { Icon } from "../../../components/icons";
import { Badge, Button, Card, Field, Loading, Segmented, Sheet, Spinner, Table, useAction, when } from "../../../components/ui";
import { envPath, get, post, useApi } from "../../../lib/api";
import { MailStatus } from "./common";

const STATUSES = [["all", "All"], ["sent", "Sent"], ["failed", "Failed"], ["suppressed", "Suppressed"], ["scheduled", "Scheduled"], ["canceled", "Canceled"]];
const SOURCES = { studio: "Studio test", composer: "Composer", api: "API", flow: "Flow", function: "Function", auth: "Sign-in" };

export function Activity({ env, openId, onOpened }) {
  const base = envPath(env);
  const [status, setStatus] = useState("all");
  const [template, setTemplate] = useState("");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(openId || null);
  useEffect(() => { if (openId) { setOpen(openId); onOpened?.(); } }, [openId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(() => setSearch(q.trim()), 300); return () => clearTimeout(t); }, [q]);
  const log = useApi(`${base}/mail/log`, { params: { limit: 200, status, ...(template ? { template } : {}), ...(search ? { q: search } : {}) }, interval: 10000 });
  const templates = useApi(`${base}/mail-templates`);

  return (
    <>
      <div className="mail-toolbar">
        <div className="mail-search"><Icon name="search" size={14} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search recipients and subjects" aria-label="Search sent mail" /></div>
        <Segmented value={status} onChange={setStatus} options={STATUSES} />
        <select value={template} onChange={(e) => setTemplate(e.target.value)} aria-label="Template">
          <option value="">Every template</option>
          {(templates.data?.data || []).map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
        </select>
      </div>
      <Card bleed flush>
        <Loading state={log} empty="Nothing has been sent from this environment yet.">
          {(data) => (
            <Table
              rows={data.data}
              empty="No message matches."
              onRowClick={(r) => setOpen(r.id)}
              columns={[
                { label: "When", render: (r) => when(r.scheduled_at && r.status === "scheduled" ? r.scheduled_at : r.created_at) },
                { label: "To", render: (r) => <span title={(r.to || []).join(", ")}>{(r.to || [])[0]}{(r.to || []).length > 1 ? ` +${r.to.length - 1}` : ""}</span> },
                { label: "Subject", render: (r) => <span className="mail-cell-subject">{r.subject || <span className="faint">(no subject)</span>}{r.attachments?.length > 0 && <span className="faint"> · 📎 {r.attachments.length}</span>}</span> },
                { label: "Template", render: (r) => (r.template ? <span className="mono">{r.template}</span> : <span className="faint">—</span>) },
                { label: "Status", render: (r) => <MailStatus value={r.status} /> },
                { label: "Source", render: (r) => <span className="muted">{SOURCES[r.source] || r.source}</span> },
              ]}
            />
          )}
        </Loading>
      </Card>
      {open && <MessageSheet base={base} id={open} onClose={() => setOpen(null)} onChanged={() => log.reload()} onOpen={setOpen} />}
    </>
  );
}

function Timeline({ m }) {
  const steps = [{ at: m.created_at, label: m.status === "scheduled" || m.scheduled_at ? "Scheduled" : "Handed to Pawabase", detail: SOURCES[m.source] || m.source }];
  if (m.scheduled_at) steps.push({ at: m.scheduled_at, label: m.status === "scheduled" ? "Will send" : "Send time", detail: "" });
  if (m.status === "canceled") steps.push({ at: m.updated_at, label: "Canceled", detail: "Never sent", tone: "muted" });
  if (m.status === "sent") steps.push({ at: m.sent_at || m.updated_at, label: "Accepted by the mail server", detail: m.attempts > 1 ? `After ${m.attempts} attempts` : "", tone: "ok" });
  if (m.status === "suppressed") steps.push({ at: m.updated_at, label: "Recorded, not delivered", detail: "No mail server, or delivery paused", tone: "warn" });
  if (m.status === "failed") steps.push({ at: m.updated_at, label: "Delivery failed", detail: m.error, tone: "bad" });
  for (const r of m.retries || []) steps.push({ at: r.created_at, label: `Retried: ${r.status}`, detail: r.error || "", tone: r.status === "failed" ? "bad" : "ok", retry: r.id });
  return (
    <ol className="mail-timeline">
      {steps.map((s, i) => (
        <li key={i} className={s.tone || ""}>
          <span className="mail-timeline-dot" />
          <span><b>{s.label}</b>{s.detail && <small>{s.detail}</small>}</span>
          <span className="faint">{s.at ? new Date(s.at).toLocaleString() : ""}</span>
        </li>
      ))}
    </ol>
  );
}

function MessageSheet({ base, id, onClose, onChanged, onOpen }) {
  const [m, setM] = useState(null);
  const [tab, setTab] = useState("preview");
  const [copyTo, setCopyTo] = useState("");
  const [run, busy] = useAction();
  const load = async () => setM(await get(`${base}/mail/log/${id}`));
  useEffect(() => { setM(null); load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  const retry = async () => { const out = await run(() => post(`${base}/mail/log/${id}/retry`), "Sent again"); if (out) { onChanged(); load(); } };
  const cancel = async () => { if (await run(() => post(`${base}/mail/log/${id}/cancel`), "Canceled")) { onChanged(); load(); } };
  const sendCopy = async () => {
    if (await run(() => post(`${base}/mail/test`, { to: copyTo.split(/[\s,;]+/).filter(Boolean), subject: m.subject, html: m.html, text: m.text || "" }), "Copy sent")) { setCopyTo(""); onChanged(); }
  };
  const canResend = m && (m.html != null || m.text != null) && m.status !== "scheduled";
  return (
    <Sheet
      title={m ? m.subject || "(no subject)" : "Message"}
      subtitle={m ? `To ${(m.to || []).join(", ")}` : ""}
      icon="mail"
      onClose={onClose}
      tabs={[{ value: "preview", label: "Preview" }, { value: "text", label: "Plain text" }, { value: "details", label: "Details" }, { value: "provider", label: "Provider response" }]}
      tab={tab}
      onTab={setTab}
      tabsPlacement="top"
      footer={m && <>
        {m.status === "scheduled" && <Button variant="danger" disabled={busy} onClick={cancel}>Cancel sending</Button>}
        {canResend && <Button disabled={busy} onClick={retry}><Icon name="refresh" />{m.status === "failed" ? "Retry" : "Send again"}</Button>}
        <Button onClick={onClose}>Close</Button>
      </>}
    >
      {!m ? <Spinner /> : (
        <>
          <div className="row wrap"><MailStatus value={m.status} />{m.template && <Badge>{m.template}</Badge>}{m.retry_of && <button type="button" className="linklike" onClick={() => onOpen(m.retry_of)}>Retry of an earlier message</button>}</div>
          {m.error && <div className="alert error">{m.error}</div>}
          {tab === "preview" && (m.html ? <iframe className="mail-message-frame" title="Message" sandbox="allow-popups" srcDoc={m.html} /> : <div className="empty">{m.text ? "This message has no HTML part; see Plain text." : "This message was logged before Studio kept message content."}</div>)}
          {tab === "text" && <pre className="mail-text-preview">{m.text || "No plain-text part."}</pre>}
          {tab === "details" && (
            <>
              <Timeline m={m} />
              <dl className="mail-dl">
                <div><dt>From</dt><dd>{m.from_email || "The environment's default sender"}</dd></div>
                <div><dt>To</dt><dd>{(m.to || []).join(", ")}</dd></div>
                {m.cc?.length > 0 && <div><dt>Cc</dt><dd>{m.cc.join(", ")}</dd></div>}
                {m.bcc?.length > 0 && <div><dt>Bcc</dt><dd>{m.bcc.join(", ")}</dd></div>}
                {m.reply_to && <div><dt>Reply to</dt><dd>{m.reply_to}</dd></div>}
                <div><dt>Source</dt><dd>{SOURCES[m.source] || m.source}</dd></div>
                {m.message_id && <div><dt>Message id</dt><dd><code>{m.message_id}</code></dd></div>}
                <div><dt>Attempts</dt><dd>{m.attempts}</dd></div>
                {m.attachments?.length > 0 && <div><dt>Attachments</dt><dd>{m.attachments.map((a) => `${a.filename} (${Math.ceil((a.size || 0) / 1024)} KB)`).join(", ")}</dd></div>}
              </dl>
              {m.data && <Field label="Template values"><pre className="code-block">{JSON.stringify(m.data, null, 2)}</pre></Field>}
              {canResend && (
                <Field label="Send a copy" hint="The same message, to any address. Logged as a Studio test.">
                  <div className="row"><input value={copyTo} onChange={(e) => setCopyTo(e.target.value)} placeholder="you@example.com" /><Button disabled={busy || !copyTo.trim()} onClick={sendCopy}>Send</Button></div>
                </Field>
              )}
            </>
          )}
          {tab === "provider" && (
            <>
              <p className="hint">What the mail server answered. A message the server accepted can still bounce later; your provider's dashboard shows bounces and complaints.</p>
              <pre className="code-block">{JSON.stringify({ status: m.status, message_id: m.message_id, error: m.error, response: m.provider_response }, null, 2)}</pre>
            </>
          )}
        </>
      )}
    </Sheet>
  );
}
