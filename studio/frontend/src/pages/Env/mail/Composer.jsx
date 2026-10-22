// Compose one email without making a template: recipients from your users,
// the same visual editor, values, attachments, a test to yourself, sending now
// or at a set time, and saving the result as a template.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../../../components/icons";
import { Button, Card, Field, Modal, Switch, useAction, useToast } from "../../../components/ui";
import { envPath, get, post } from "../../../lib/api";
import { attachmentFrom, pickAndUpload } from "./api";
import { BlockInspector, EmailCanvas, InsertMenu, useMailContext, useMailEditor } from "./editor/EmailEditor";
import { DEFAULT_THEME, renderEmail, sampleData, usedVariables } from "./editor/render";
import { TEMPLATE_NAME, TestModal, VariablePicker } from "./TemplateWorkspace";

const STORE = (env) => `pawabase.mail.compose.${env}`;
const MAX_ATTACHMENTS = 10 * 1024 * 1024;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const blankDoc = () => ({ type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "Hi " }, { type: "variable", attrs: { name: "name", fallback: "there" } }, { type: "text", text: "," }] }, { type: "paragraph" }] });

function restore(env) {
  try { return JSON.parse(localStorage.getItem(STORE(env)) || "null"); } catch { return null; }
}

/** Addresses as chips, with suggestions from the environment's users. */
function Recipients({ env, value, onChange, placeholder }) {
  const [text, setText] = useState("");
  const [found, setFound] = useState([]);
  useEffect(() => {
    if (text.trim().length < 2) { setFound([]); return undefined; }
    const t = setTimeout(async () => {
      try {
        const out = await get(`/envs/${env}/users`, { service: "auth", params: { search: text.trim(), limit: 6 } });
        setFound((out?.data || []).filter((u) => u.email && !value.includes(u.email)));
      } catch { setFound([]); }
    }, 200);
    return () => clearTimeout(t);
  }, [env, text, value]);
  const add = (addresses) => {
    const next = addresses.split(/[\s,;]+/).map((a) => a.trim()).filter(Boolean);
    onChange([...new Set([...value, ...next])]);
    setText("");
    setFound([]);
  };
  return (
    <div className="mail-recipients">
      {value.map((a) => <span key={a} className={`mail-tag ${EMAIL.test(a) ? "" : "invalid"}`}>{a}<button type="button" onClick={() => onChange(value.filter((x) => x !== a))} aria-label={`Remove ${a}`}>×</button></span>)}
      <input value={text} placeholder={value.length ? "" : placeholder} onChange={(e) => setText(e.target.value)} onBlur={() => text.trim() && add(text)}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === "," || e.key === "Tab") && text.trim()) { e.preventDefault(); add(text); }
          if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
        }} />
      {found.length > 0 && (
        <div className="mail-recipient-menu">
          {found.map((u) => <button key={u.id} type="button" onMouseDown={(e) => { e.preventDefault(); add(u.email); }}><b>{u.name || u.username || u.email}</b><small>{u.email}</small></button>)}
        </div>
      )}
    </div>
  );
}

/** A date as a datetime-local input value, in the reader's time zone. */
const localInput = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

export function Composer({ env, kit, senders, defaultFrom, onSent, onSavedTemplate }) {
  const base = envPath(env);
  const toast = useToast();
  const saved = useMemo(() => restore(env), [env]);
  const [from, setFrom] = useState(saved?.from || "");
  const [to, setTo] = useState(saved?.to || []);
  const [cc, setCc] = useState(saved?.cc || []);
  const [bcc, setBcc] = useState(saved?.bcc || []);
  const [showCc, setShowCc] = useState(Boolean(saved?.cc?.length || saved?.bcc?.length));
  const [replyTo, setReplyTo] = useState(saved?.replyTo || "");
  const [subject, setSubject] = useState(saved?.subject || "");
  const [preheader, setPreheader] = useState(saved?.preheader || "");
  const [doc, setDoc] = useState(saved?.doc || blankDoc());
  const [theme, setTheme] = useState({ ...DEFAULT_THEME, ...(kit?.brand || {}), ...(saved?.theme || {}) });
  const [values, setValues] = useState(saved?.values || { name: "" });
  const [files, setFiles] = useState([]);
  const [later, setLater] = useState(false);
  const [at, setAt] = useState("");
  const [modal, setModal] = useState(null);
  const [run, busy] = useAction();
  const fileInput = useRef(null);

  const pickImage = useCallback(async () => { try { return await pickAndUpload(env); } catch (e) { toast(e.message, "error"); return null; } }, [env, toast]);
  const editor = useMailEditor({ doc, onChange: setDoc, theme, onPickImage: pickImage });
  const used = useMemo(() => usedVariables(doc, subject, preheader), [doc, subject, preheader]);
  const variables = useMemo(() => used.map((name) => ({ name, sample: values[name] ?? "" })), [used, values]);
  const addVariable = useCallback((name) => setValues((v) => ({ ...v, [name]: v[name] ?? "" })), []);
  useMailContext(editor, { variables, blocks: kit?.blocks || [], onCreateVariable: addVariable });

  // The message in progress survives a reload: kept in this browser only.
  useEffect(() => {
    const t = setTimeout(() => { try { localStorage.setItem(STORE(env), JSON.stringify({ from, to, cc, bcc, replyTo, subject, preheader, doc, theme, values })); } catch { /* full or private */ } }, 500);
    return () => clearTimeout(t);
  }, [env, from, to, cc, bcc, replyTo, subject, preheader, doc, theme, values]);

  const rendered = useMemo(() => renderEmail({ doc, theme, subject, preheader }), [doc, theme, subject, preheader]);
  const totalSize = files.reduce((n, f) => n + f.size, 0);
  const invalid = [...to, ...cc, ...bcc].filter((a) => !EMAIL.test(a));

  const loadTemplate = async (name) => {
    if (!name) return;
    const t = await get(`${base}/mail-templates/${encodeURIComponent(name)}`);
    const source = t.draft?.design ? t.draft : t;
    if (!source.design?.doc) { toast("That template was written in HTML; open it in the template editor to convert it first.", "error"); return; }
    editor.commands.setContent(source.design.doc, { emitUpdate: true });
    setTheme({ ...DEFAULT_THEME, ...(source.design.theme || {}) });
    setSubject(source.subject || "");
    setPreheader(source.preheader || "");
    setValues(Object.fromEntries((t.variables || []).map((v) => [v.name, v.sample ?? ""])));
    toast(`Loaded ${name}`);
  };

  const addFiles = async (list) => {
    const next = await Promise.all([...list].map(attachmentFrom));
    setFiles((f) => [...f, ...next]);
  };

  const send = async () => {
    const data = sampleData(variables);
    const result = await run(async () => {
      const filled = await post(`${base}/mail/render`, { subject, html: rendered.html, text: rendered.text, data });
      return post(`${base}/mail/send`, {
        to, cc, bcc, subject: filled.subject, html: filled.html, text: filled.text, data,
        from_email: from || null, reply_to: replyTo || null,
        attachments: files.map(({ filename, content_type, content }) => ({ filename, content_type, content })),
        scheduled_at: later && at ? new Date(at).toISOString() : null,
      });
    });
    if (result) {
      toast(result.scheduled ? `Scheduled for ${new Date(result.scheduled_at).toLocaleString()}` : result.suppressed ? "Recorded, not delivered: no provider is set up or delivery is paused" : `Sent to ${(result.to || to).join(", ")}`, result.suppressed ? "warn" : undefined);
      try { localStorage.removeItem(STORE(env)); } catch { /* ignore */ }
      onSent?.(result);
    }
  };

  const canSend = to.length > 0 && !invalid.length && subject.trim() && totalSize <= MAX_ATTACHMENTS && (!later || (at && new Date(at) > new Date()));

  return (
    <div className="mail-compose">
      <Card flush className="mail-compose-head">
        <div className="mail-compose-fields">
          <label><span>From</span>
            <select value={from} onChange={(e) => setFrom(e.target.value)}>
              <option value="">{defaultFrom ? `Default · ${defaultFrom}` : "The environment's default sender"}</option>
              {senders.map((s) => <option key={s.email} value={s.name ? `${s.name} <${s.email}>` : s.email}>{s.name ? `${s.name} <${s.email}>` : s.email}</option>)}
            </select>
          </label>
          <label><span>To</span><Recipients env={env} value={to} onChange={setTo} placeholder="Type an address or search your users" />{!showCc && <button type="button" className="linklike" onClick={() => setShowCc(true)}>Cc / Bcc</button>}</label>
          {showCc && <label><span>Cc</span><Recipients env={env} value={cc} onChange={setCc} placeholder="Copied" /></label>}
          {showCc && <label><span>Bcc</span><Recipients env={env} value={bcc} onChange={setBcc} placeholder="Copied without the others seeing" /></label>}
          <label><span>Reply to</span><input value={replyTo} onChange={(e) => setReplyTo(e.target.value)} placeholder="Optional: where replies go" /></label>
          <label><span>Subject</span><input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject" /><VariablePicker variables={variables} onPick={(n) => setSubject((s) => `${s}{{ ${n} }}`)} label="Insert a value" /></label>
          <label><span>Preview text</span><input value={preheader} onChange={(e) => setPreheader(e.target.value)} placeholder="Optional: shown after the subject in most inboxes" /></label>
        </div>
        <div className="mail-compose-tools">
          <TemplateSelect base={base} onPick={loadTemplate} />
          <InsertMenu editor={editor} blocks={kit?.blocks || []} />
          <button type="button" className="mail-tool icon" title="Undo" disabled={!editor?.can().undo()} onClick={() => editor.chain().focus().undo().run()}><Icon name="undo" size={15} /></button>
          <button type="button" className="mail-tool icon" title="Redo" disabled={!editor?.can().redo()} onClick={() => editor.chain().focus().redo().run()}><Icon name="redo" size={15} /></button>
          <span className="grow" />
          <button type="button" className="mail-tool" onClick={() => { if (confirm("Clear this message?")) { editor.commands.setContent(blankDoc(), { emitUpdate: true }); setTo([]); setCc([]); setBcc([]); setSubject(""); setPreheader(""); setFiles([]); setValues({ name: "" }); } }}>Clear</button>
        </div>
      </Card>

      <div className="mail-compose-body">
        <div className="mail-compose-canvas"><EmailCanvas editor={editor} theme={theme} palette={(kit?.palettes || []).flatMap((p) => p.colors || [])} /></div>
        <aside className="mail-compose-side">
          <Card title="Block" className="mail-compose-card"><BlockInspector editor={editor} palette={(kit?.palettes || []).flatMap((p) => p.colors || [])} onPickImage={pickImage} variables={variables} /></Card>
          {used.length > 0 && (
            <Card title="Values" description="Filled in before sending." className="mail-compose-card">
              <div className="stack sm">
                {used.map((name) => <Field key={name} label={<span className="mono">{name}</span>}><input value={values[name] ?? ""} onChange={(e) => setValues((v) => ({ ...v, [name]: e.target.value }))} /></Field>)}
              </div>
            </Card>
          )}
          <Card title="Attachments" description={`${Math.round(totalSize / 1024)} KB of 10 MB`} className="mail-compose-card">
            <div className="stack sm">
              {files.map((f, i) => <div key={i} className="mail-file"><Icon name="book" size={14} /><span className="grow">{f.filename}</span><small>{Math.ceil(f.size / 1024)} KB</small><button type="button" className="mail-icon-btn" onClick={() => setFiles((l) => l.filter((_, j) => j !== i))}><Icon name="x" size={12} /></button></div>)}
              {totalSize > MAX_ATTACHMENTS && <div className="alert error">Attachments may total at most 10 MB.</div>}
              <Button size="sm" onClick={() => fileInput.current?.click()}><Icon name="upload" />Attach files</Button>
              <input ref={fileInput} type="file" multiple hidden onChange={(e) => { addFiles(e.target.files || []); e.target.value = ""; }} />
            </div>
          </Card>
          <Card title="Send" className="mail-compose-card">
            <div className="stack sm">
              <Switch checked={later} onChange={setLater} label="Send later" />
              {later && <Field label="Send at" hint="Your local time."><input type="datetime-local" value={at} min={localInput(new Date(Date.now() + 60000))} onChange={(e) => setAt(e.target.value)} /></Field>}
              {invalid.length > 0 && <div className="alert error">Not an email address: {invalid.join(", ")}</div>}
              <Button variant="primary" disabled={busy || !canSend} onClick={send}><Icon name={later ? "clock" : "send"} />{busy ? "Sending…" : later ? "Schedule" : "Send now"}</Button>
              <Button onClick={() => setModal("test")}>Send a test to yourself</Button>
              <Button variant="ghost" onClick={() => setModal("template")}>Save as template</Button>
            </div>
          </Card>
        </aside>
      </div>
      {modal === "test" && <TestModal base={base} rendered={rendered} subject={subject} variables={variables} onClose={() => setModal(null)} />}
      {modal === "template" && <SaveTemplateModal base={base} subject={subject} preheader={preheader} rendered={rendered} doc={doc} theme={theme} variables={variables} onClose={() => setModal(null)} onSaved={onSavedTemplate} />}
    </div>
  );
}

function TemplateSelect({ base, onPick }) {
  const [list, setList] = useState([]);
  useEffect(() => { get(`${base}/mail-templates`).then((out) => setList(out?.data || [])).catch(() => setList([])); }, [base]);
  return (
    <select className="mail-template-select" value="" onChange={(e) => onPick(e.target.value)} aria-label="Start from a template">
      <option value="">Start from a template…</option>
      {list.filter((t) => t.status !== "archived").map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
    </select>
  );
}

function SaveTemplateModal({ base, subject, preheader, rendered, doc, theme, variables, onClose, onSaved }) {
  const [name, setName] = useState("");
  const [run, busy] = useAction();
  const save = async () => {
    const created = await run(() => post(`${base}/mail-templates`, {
      name, status: "draft", subject: "", preheader: "", html: "", text: "",
      variables: variables.map((v) => ({ name: v.name, sample: v.sample, description: "" })),
      draft: { subject, preheader, html: rendered.html, text: rendered.text, design: { doc, theme }, saved_at: new Date().toISOString() },
    }), "Saved as a template");
    if (created) { onClose(); onSaved?.(created.name); }
  };
  return (
    <Modal title="Save as template" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !TEMPLATE_NAME.test(name)} onClick={save}>Save</Button></>}>
      <Field label="Template name" hint="Lowercase letters, numbers, dashes and underscores. It starts as a draft."><input className="mono" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="monthly_update" /></Field>
    </Modal>
  );
}
