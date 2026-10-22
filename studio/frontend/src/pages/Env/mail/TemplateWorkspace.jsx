// The template workspace: the visual editor with everything a template needs
// around it — subject and preview text, autosaved drafts, publishing, version
// history, sample values, checks, test sends and HTML import/export.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../../../components/icons";
import { Badge, Button, Field, Modal, Popover, Segmented, Spinner, Switch, useAction, useToast, when } from "../../../components/ui";
import { envPath, get, post, put, useApi } from "../../../lib/api";
import { download, pickAndUpload } from "./api";
import { CHECK_TONES, CheckList } from "./common";
import { checkEmail } from "./editor/checks";
import { BlockInspector, ColorField, EmailCanvas, InsertMenu, useMailContext, useMailEditor } from "./editor/EmailEditor";
import { DEFAULT_THEME, FONTS, renderEmail, sampleData, usedVariables, validVariable } from "./editor/render";
import { CATEGORIES } from "./editor/starters";

export const TEMPLATE_NAME = /^[a-z][a-z0-9_-]{0,62}$/;

const TABS = [["block", "Block"], ["style", "Style"], ["values", "Values"], ["checks", "Checks"], ["history", "History"], ["details", "Details"]];

/** A template as the editor opens it: the draft when there is one, else the published version. */
function working(tpl) {
  const source = tpl.draft || tpl;
  const design = source.design || tpl.design;
  if (design?.doc) return { subject: source.subject || "", preheader: source.preheader || "", design, legacy: false };
  // Written as HTML before the visual editor existed: kept exactly, as one HTML block, until converted.
  const html = source.html || tpl.html || "";
  const body = (/<body[^>]*>([\s\S]*)<\/body>/i.exec(html) || [null, html])[1];
  return {
    subject: source.subject || "",
    preheader: source.preheader || "",
    design: { doc: { type: "doc", content: body.trim() ? [{ type: "htmlBlock", attrs: { html: body } }] : [{ type: "paragraph" }] }, theme: {} },
    legacy: Boolean(body.trim()),
  };
}

export function TemplateWorkspace({ env, name, kit, onClose, onRenamed, onDuplicated }) {
  const base = envPath(env);
  const toast = useToast();
  const [tpl, setTpl] = useState(null);
  const [error, setError] = useState(null);
  const reload = useCallback(async () => {
    try { setTpl(await get(`${base}/mail-templates/${encodeURIComponent(name)}`)); } catch (e) { setError(e); }
  }, [base, name]);
  useEffect(() => { reload(); }, [reload]);
  if (error) return <div className="mail-workspace"><div className="mail-workspace-error"><p>{error.message}</p><Button onClick={onClose}>Back to templates</Button></div></div>;
  if (!tpl) return <div className="mail-workspace"><div className="mail-workspace-error"><Spinner /></div></div>;
  return <Workspace key={tpl.name + (tpl.updated_at || "")} env={env} base={base} tpl={tpl} kit={kit} toast={toast} reload={reload} onClose={onClose} onRenamed={onRenamed} onDuplicated={onDuplicated} />;
}

function Workspace({ env, base, tpl, kit, toast, reload, onClose, onRenamed, onDuplicated }) {
  const initial = useMemo(() => working(tpl), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [subject, setSubject] = useState(initial.subject);
  const [preheader, setPreheader] = useState(initial.preheader);
  const [theme, setTheme] = useState({ ...DEFAULT_THEME, ...(kit?.brand || {}), ...(initial.design.theme || {}) });
  const [doc, setDoc] = useState(initial.design.doc);
  const [variables, setVariables] = useState(tpl.variables || []);
  const [meta, setMeta] = useState({ description: tpl.description || "", category: tpl.category || "", folder: tpl.folder || "", tags: tpl.tags || [], favorite: Boolean(tpl.favorite) });
  const [legacy, setLegacy] = useState(initial.legacy);
  const [tab, setTab] = useState("block");
  const [mode, setMode] = useState("edit");
  const [device, setDevice] = useState("desktop");
  const [saveState, setSaveState] = useState({ status: "saved", at: tpl.draft?.saved_at || tpl.updated_at });
  const [hasDraft, setHasDraft] = useState(Boolean(tpl.draft));
  const [modal, setModal] = useState(null);
  const palette = useMemo(() => [...new Set([theme.accent, theme.text, theme.link, ...((kit?.palettes || []).flatMap((p) => p.colors || []))].filter(Boolean))], [theme, kit]);

  const pickImage = useCallback(async () => {
    try { return await pickAndUpload(env); } catch (e) { toast(e.message, "error"); return null; }
  }, [env, toast]);
  const editor = useMailEditor({ doc: initial.design.doc, onChange: setDoc, theme, onPickImage: pickImage });
  const addVariable = useCallback((name) => setVariables((list) => (list.some((v) => v.name === name) ? list : [...list, { name, description: "", sample: "" }])), []);
  useMailContext(editor, { variables, blocks: kit?.blocks || [], onCreateVariable: addVariable });

  const rendered = useMemo(() => renderEmail({ doc, theme, subject, preheader }), [doc, theme, subject, preheader]);
  const checks = useMemo(() => checkEmail({ doc, theme, subject, preheader, variables, html: rendered.html }), [doc, theme, subject, preheader, variables, rendered.html]);
  const problems = checks.filter((c) => c.level !== "info").length;

  // ── saving ──
  // The published version as the server last reported it: autosave sends it back unchanged, so it must follow publishes.
  const [live, setLive] = useState(tpl);
  const body = useCallback((overrides = {}) => ({
    name: tpl.name,
    description: meta.description,
    subject: live.subject || "",
    preheader: live.preheader || "",
    html: live.html || "",
    text: live.text || "",
    design: live.design ?? null,
    status: live.status,
    category: meta.category,
    tags: meta.tags,
    folder: meta.folder,
    favorite: meta.favorite,
    variables,
    draft: { subject, preheader, html: rendered.html, text: rendered.text, design: { doc, theme }, saved_at: new Date().toISOString() },
    ...overrides,
  }), [tpl, live, meta, variables, subject, preheader, rendered, doc, theme]);

  const latest = useRef(body);
  latest.current = body;
  const firstRender = useRef(true);
  const timer = useRef(null);
  const save = useCallback(async () => {
    clearTimeout(timer.current);
    setSaveState({ status: "saving" });
    try {
      const saved = await put(`${base}/mail-templates/${encodeURIComponent(tpl.name)}`, latest.current());
      setLive(saved);
      setSaveState({ status: "saved", at: saved.draft?.saved_at || new Date().toISOString() });
      setHasDraft(true);
      return true;
    } catch (e) {
      setSaveState({ status: "error", message: e.message });
      return false;
    }
  }, [base, tpl.name]);
  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return; }
    setSaveState((s) => (s.status === "saving" ? s : { status: "dirty" }));
    clearTimeout(timer.current);
    timer.current = setTimeout(save, 1200);
    return () => clearTimeout(timer.current);
  }, [doc, theme, subject, preheader, variables, meta, save]);

  const [publishing, setPublishing] = useState(false);
  const publish = async (note = "") => {
    setPublishing(true);
    try {
      if (!(await save())) return;
      const result = await post(`${base}/mail-templates/${encodeURIComponent(tpl.name)}/publish`, { note });
      setLive(result);
      setHasDraft(false);
      toast(`Published version ${result.version}`);
      setModal(null);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setPublishing(false);
    }
  };

  // ── keyboard ──
  useEffect(() => {
    const onKey = (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      if (e.key === "s") { e.preventDefault(); save(); }
      else if (e.key === "Enter") { e.preventDefault(); setModal({ kind: "test" }); }
      else if (e.key === "/") { e.preventDefault(); setModal({ kind: "shortcuts" }); }
      else if (e.shiftKey && (e.key === "p" || e.key === "P")) { e.preventDefault(); setModal({ kind: "publish" }); }
      else if (e.shiftKey && (e.key === "e" || e.key === "E")) { e.preventDefault(); setMode((m) => (m === "edit" ? "preview" : "edit")); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  const status = live.status === "archived" ? ["Archived", ""] : live.status === "draft" && !live.html ? ["Draft", "yellow"] : hasDraft ? ["Unpublished changes", "yellow"] : ["Published", "green"];
  const saveLabel = saveState.status === "saving" ? "Saving…" : saveState.status === "dirty" ? "Unsaved changes" : saveState.status === "error" ? `Not saved: ${saveState.message}` : saveState.at ? `Saved ${when(saveState.at)}` : "Saved";

  const convertLegacy = () => {
    const html = doc.content?.[0]?.attrs?.html || "";
    editor.commands.setContent(html, { emitUpdate: true });
    setLegacy(false);
    toast("Converted into editable blocks. Check the result; undo puts the HTML back.");
  };

  return (
    <div className="mail-workspace" role="dialog" aria-label={`Editing ${tpl.name}`}>
      <header className="mail-ws-bar">
        <button type="button" className="mail-tool" onClick={async () => { await save(); onClose(); }} title="Back to templates"><Icon name="x" size={15} />Close</button>
        <div className="mail-ws-title">
          <b className="mono">{tpl.name}</b>
          <Badge tone={status[1]}>{status[0]}</Badge>
          <span className={`mail-save ${saveState.status}`}>{saveLabel}</span>
        </div>
        <div className="mail-ws-tools">
          <button type="button" className="mail-tool icon" title="Undo (⌘Z)" disabled={!editor?.can().undo()} onClick={() => editor.chain().focus().undo().run()}><Icon name="undo" size={15} /></button>
          <button type="button" className="mail-tool icon" title="Redo (⌘⇧Z)" disabled={!editor?.can().redo()} onClick={() => editor.chain().focus().redo().run()}><Icon name="redo" size={15} /></button>
          <InsertMenu editor={editor} blocks={kit?.blocks || []} />
          <Segmented value={mode} onChange={setMode} options={[["edit", "Edit"], ["preview", "Preview"]]} />
          {mode === "preview" && <Segmented value={device} onChange={setDevice} options={[["desktop", "Desktop"], ["mobile", "Mobile"], ["text", "Plain text"]]} />}
        </div>
        <div className="mail-ws-actions">
          <Popover align="end" width={240} label="More" trigger={(props) => <button type="button" className="mail-tool" {...props}>More</button>}>
            {({ close }) => (<div className="mail-more" onClick={(e) => e.target.closest("button") && close(false)}>
              <button type="button" onClick={() => setModal({ kind: "import" })}><Icon name="upload" size={14} />Import HTML</button>
              <button type="button" onClick={() => download(`${tpl.name}.html`, rendered.html)}><Icon name="external" size={14} />Export HTML</button>
              <button type="button" onClick={() => { navigator.clipboard.writeText(rendered.html); toast("HTML copied"); }}><Icon name="copy" size={14} />Copy HTML</button>
              <button type="button" onClick={() => setModal({ kind: "duplicate" })}><Icon name="layers" size={14} />Duplicate</button>
              <button type="button" onClick={() => setTab("history")}><Icon name="clock" size={14} />Version history</button>
              <button type="button" onClick={() => setModal({ kind: "shortcuts" })}><Icon name="help" size={14} />Keyboard shortcuts</button>
            </div>)}
          </Popover>
          <Button onClick={() => setModal({ kind: "test" })}><Icon name="send" />Send test</Button>
          <Button variant="primary" disabled={publishing} onClick={() => setModal({ kind: "publish" })}>{publishing ? "Publishing…" : "Publish"}</Button>
        </div>
      </header>

      <div className="mail-ws-fields">
        <label><span>Subject</span><input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="What the inbox shows first" /></label>
        <label><span>Preview text</span><input value={preheader} onChange={(e) => setPreheader(e.target.value)} placeholder="The line shown after the subject in most inboxes" /></label>
        <VariablePicker variables={variables} onPick={(name) => setSubject((s) => `${s}{{ ${name} }}`)} label="Insert a value into the subject" />
      </div>

      {legacy && (
        <div className="mail-ws-banner">
          This template was written in HTML. It is kept exactly as written, in one HTML block.
          <Button size="sm" onClick={convertLegacy}>Convert to editable blocks</Button>
        </div>
      )}

      <div className="mail-ws-body">
        <main className="mail-ws-stage">
          {mode === "edit" ? <EmailCanvas editor={editor} theme={theme} palette={palette} /> : <Preview base={base} rendered={rendered} subject={subject} variables={variables} device={device} />}
        </main>
        <aside className="mail-ws-side">
          <div className="mail-ws-tabs" role="tablist">
            {TABS.map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
                {label}{key === "checks" && problems > 0 && <span className="mail-count">{problems}</span>}
              </button>
            ))}
          </div>
          <div className="mail-ws-panel">
            {tab === "block" && <BlockInspector editor={editor} palette={palette} onPickImage={pickImage} variables={variables} onSaveBlock={(content) => setModal({ kind: "save-block", content })} />}
            {tab === "style" && <StylePanel theme={theme} setTheme={setTheme} palette={palette} kit={kit} />}
            {tab === "values" && <ValuesPanel variables={variables} setVariables={setVariables} used={usedVariables(doc, subject, preheader)} />}
            {tab === "checks" && <CheckList checks={checks} />}
            {tab === "history" && <HistoryPanel base={base} name={tpl.name} onRestored={reload} />}
            {tab === "details" && <DetailsPanel base={base} tpl={tpl} meta={meta} setMeta={setMeta} kit={kit} onRenamed={onRenamed} />}
          </div>
        </aside>
      </div>

      {modal?.kind === "test" && <TestModal base={base} rendered={rendered} subject={subject} variables={variables} onClose={() => setModal(null)} />}
      {modal?.kind === "publish" && <PublishModal checks={checks} busy={publishing} onPublish={publish} onClose={() => setModal(null)} />}
      {modal?.kind === "import" && <ImportModal editor={editor} onImported={() => setLegacy(false)} onClose={() => setModal(null)} />}
      {modal?.kind === "shortcuts" && <ShortcutsModal onClose={() => setModal(null)} />}
      {modal?.kind === "duplicate" && <DuplicateModal base={base} body={() => latest.current()} onClose={() => setModal(null)} onDone={(name) => { setModal(null); onDuplicated?.(name); }} />}
      {modal?.kind === "save-block" && <SaveBlockModal env={env} kit={kit} content={modal.content} onClose={() => setModal(null)} />}
    </div>
  );
}

// ── insert a value into a text field ──────────────────────────────────

export function VariablePicker({ variables, onPick, label }) {
  return (
    <Popover align="end" width={260} label={label} trigger={(props) => <button type="button" className="mail-tool icon" title={label} {...props}>{"{}"}</button>}>
      {({ close }) => (<div className="mail-more" onClick={(e) => e.target.closest("button") && close(false)}>
        {variables.length === 0 && <p className="hint">No values yet. Add them under Values.</p>}
        {variables.map((v) => <button key={v.name} type="button" onClick={() => onPick(v.name)}><span className="mono">{v.name}</span></button>)}
      </div>)}
    </Popover>
  );
}

// ── preview ───────────────────────────────────────────────────────────

/** The email as recipients get it: rendered on the server with the sample values, exactly as sending does. */
function Preview({ base, rendered, subject, variables, device }) {
  const [out, setOut] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let live = true;
    const t = setTimeout(async () => {
      try {
        const result = await post(`${base}/mail/render`, { subject, html: rendered.html, text: rendered.text, data: sampleData(variables) });
        if (live) { setOut(result); setErr(null); }
      } catch (e) { if (live) setErr(e.message); }
    }, 250);
    return () => { live = false; clearTimeout(t); };
  }, [base, rendered, subject, variables]);
  if (err) return <div className="mail-preview"><div className="alert error">{err}</div></div>;
  if (!out) return <div className="mail-preview"><Spinner /></div>;
  return (
    <div className="mail-preview">
      <div className="mail-inbox-line"><b>{out.subject || "(no subject)"}</b></div>
      {device === "text" ? (
        <pre className="mail-text-preview">{out.text}</pre>
      ) : (
        <div className={`mail-device ${device}`}>
          <iframe title="Email preview" sandbox="allow-popups" srcDoc={out.html} />
        </div>
      )}
    </div>
  );
}

// ── style ─────────────────────────────────────────────────────────────

function StylePanel({ theme, setTheme, palette, kit }) {
  const set = (key) => (value) => setTheme((t) => ({ ...t, [key]: value }));
  return (
    <div className="mail-inspector">
      {kit?.brand && Object.keys(kit.brand).length > 0 && (
        <Button size="sm" onClick={() => setTheme((t) => ({ ...t, ...kit.brand }))}><Icon name="sparkle" />Apply brand kit</Button>
      )}
      <label className="mail-row"><span className="mail-row-label">Font</span>
        <select value={theme.font} onChange={(e) => set("font")(e.target.value)}>{FONTS.map(([n]) => <option key={n} value={n}>{n}</option>)}</select>
      </label>
      <label className="mail-row"><span className="mail-row-label">Text size</span>
        <Segmented value={String(theme.fontSize)} onChange={(v) => set("fontSize")(Number(v))} options={[["14", "14"], ["15", "15"], ["16", "16"], ["17", "17"]]} />
      </label>
      {[["text", "Text"], ["heading", "Headings"], ["link", "Links"], ["accent", "Buttons"], ["accentText", "Button text"], ["content", "Email background"], ["background", "Page background"], ["border", "Lines"]].map(([key, label]) => (
        <label key={key} className="mail-row"><span className="mail-row-label">{label}</span><ColorField value={theme[key]} onChange={set(key)} palette={palette} allowEmpty={false} /></label>
      ))}
      <label className="mail-row"><span className="mail-row-label">Width</span>
        <Segmented value={String(theme.width)} onChange={(v) => set("width")(Number(v))} options={[["520", "Narrow"], ["600", "Standard"], ["680", "Wide"]]} />
      </label>
      <label className="mail-row"><span className="mail-row-label">Padding · {theme.padding}px</span>
        <input type="range" min={16} max={64} step={4} value={theme.padding} onChange={(e) => set("padding")(Number(e.target.value))} />
      </label>
      <label className="mail-row"><span className="mail-row-label">Corners · {theme.radius}px</span>
        <input type="range" min={0} max={24} value={theme.radius} onChange={(e) => set("radius")(Number(e.target.value))} />
      </label>
      <Button size="sm" variant="ghost" onClick={() => setTheme({ ...DEFAULT_THEME, ...(kit?.brand || {}) })}>Reset styles</Button>
    </div>
  );
}

// ── values ────────────────────────────────────────────────────────────

function ValuesPanel({ variables, setVariables, used }) {
  const update = (i, patch) => setVariables((list) => list.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const missing = used.filter((name) => !variables.some((v) => v.name === name || v.name.startsWith(`${name}.`) || name.startsWith(`${v.name}.`)));
  return (
    <div className="mail-inspector">
      <p className="mail-row-hint">The values this template reads when it is sent. Sample values fill previews and test sends; your code or flow passes the real ones.</p>
      {missing.length > 0 && (
        <div className="mail-missing">
          <b>Used but not defined</b>
          {missing.map((name) => <button key={name} type="button" className="mono" onClick={() => setVariables((l) => [...l, { name, description: "", sample: "" }])}>+ {name}</button>)}
        </div>
      )}
      {variables.map((v, i) => (
        <div key={i} className="mail-var-row">
          <div className="row">
            <input className={`mono ${validVariable(v.name) ? "" : "invalid"}`} value={v.name} onChange={(e) => update(i, { name: e.target.value.trim() })} placeholder="user.name" />
            <button type="button" className="mail-icon-btn danger" title="Remove" onClick={() => setVariables((l) => l.filter((_, j) => j !== i))}><Icon name="trash" size={13} /></button>
          </div>
          <input value={v.sample ?? ""} onChange={(e) => update(i, { sample: e.target.value })} placeholder="Sample value (JSON for lists)" />
          <input value={v.description ?? ""} onChange={(e) => update(i, { description: e.target.value })} placeholder="What it is (optional)" />
        </div>
      ))}
      <Button size="sm" onClick={() => setVariables((l) => [...l, { name: "", description: "", sample: "" }])}><Icon name="plus" />Add a value</Button>
    </div>
  );
}

// ── history ───────────────────────────────────────────────────────────

function HistoryPanel({ base, name, onRestored }) {
  const versions = useApi(`${base}/mail-templates/${encodeURIComponent(name)}/versions`);
  const [open, setOpen] = useState(null);
  const [run, busy] = useAction();
  const view = async (number) => setOpen(await get(`${base}/mail-templates/${encodeURIComponent(name)}/versions/${number}`));
  const restore = async (number) => {
    if (await run(() => post(`${base}/mail-templates/${encodeURIComponent(name)}/versions/${number}/restore`), `Version ${number} is now the draft`)) onRestored();
  };
  const rows = versions.data?.data || [];
  return (
    <div className="mail-inspector">
      <p className="mail-row-hint">Each publish keeps a version. Restoring one makes it the draft; publish it to send it again.</p>
      {versions.loading && <Spinner />}
      {!versions.loading && rows.length === 0 && <p className="mail-row-hint">Nothing published yet.</p>}
      <div className="mail-versions">
        {rows.map((v, i) => (
          <div key={v.number} className={`mail-version ${open?.number === v.number ? "on" : ""}`}>
            <button type="button" className="mail-version-main" onClick={() => view(v.number)}>
              <span className="row"><b>Version {v.number}</b>{i === 0 && <Badge tone="green">Live</Badge>}</span>
              <small>{when(v.created_at)}{v.note ? ` · ${v.note}` : ""}</small>
            </button>
            <Button size="sm" disabled={busy} onClick={() => restore(v.number)}>Restore</Button>
          </div>
        ))}
      </div>
      {open && (
        <div className="mail-version-preview">
          <div className="row"><b>Version {open.number}</b><span className="grow" /><button type="button" className="mail-icon-btn" onClick={() => setOpen(null)}><Icon name="x" size={13} /></button></div>
          <div className="mail-inbox-line small">{open.subject}</div>
          <iframe title={`Version ${open.number}`} sandbox="" srcDoc={open.html} />
        </div>
      )}
    </div>
  );
}

// ── details ───────────────────────────────────────────────────────────

function DetailsPanel({ base, tpl, meta, setMeta, kit, onRenamed }) {
  const [name, setName] = useState(tpl.name);
  const [tag, setTag] = useState("");
  const [run, busy] = useAction();
  const usage = useApi(`${base}/mail/templates/usage`);
  const mine = usage.data?.data?.[tpl.name];
  const rename = async () => {
    const result = await run(() => post(`${base}/mail-templates/${encodeURIComponent(tpl.name)}/rename`, { to: name }), "Renamed");
    if (result) onRenamed?.(result.name, result.flows_to_update || []);
  };
  const folders = [...new Set([...(kit?.folders || []), meta.folder].filter(Boolean))];
  return (
    <div className="mail-inspector">
      <label className="mail-row"><span className="mail-row-label">Name</span>
        <div className="row"><input className="mono" value={name} onChange={(e) => setName(e.target.value)} /><Button size="sm" disabled={busy || name === tpl.name || !name} onClick={rename}>Rename</Button></div>
        <span className="mail-row-hint">Code and flows send a template by this name.</span>
      </label>
      <label className="mail-row"><span className="mail-row-label">Description</span><input value={meta.description} onChange={(e) => setMeta((m) => ({ ...m, description: e.target.value }))} /></label>
      <label className="mail-row"><span className="mail-row-label">Category</span>
        <select value={meta.category} onChange={(e) => setMeta((m) => ({ ...m, category: e.target.value }))}><option value="">None</option>{CATEGORIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      </label>
      <label className="mail-row"><span className="mail-row-label">Folder</span>
        <input list="mail-folders" value={meta.folder} onChange={(e) => setMeta((m) => ({ ...m, folder: e.target.value }))} placeholder="No folder" />
        <datalist id="mail-folders">{folders.map((f) => <option key={f} value={f} />)}</datalist>
      </label>
      <label className="mail-row"><span className="mail-row-label">Tags</span>
        <div className="mail-tags">
          {meta.tags.map((t) => <span key={t} className="mail-tag">{t}<button type="button" onClick={() => setMeta((m) => ({ ...m, tags: m.tags.filter((x) => x !== t) }))}>×</button></span>)}
          <input value={tag} placeholder="Add a tag" onChange={(e) => setTag(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && tag.trim()) { e.preventDefault(); setMeta((m) => ({ ...m, tags: [...new Set([...m.tags, tag.trim()])] })); setTag(""); } }} />
        </div>
      </label>
      <Switch checked={meta.favorite} onChange={(v) => setMeta((m) => ({ ...m, favorite: v }))} label="Favourite" />
      <div className="mail-usage">
        <b>Usage</b>
        {mine ? (
          <>
            <span>{mine.sent} sent{mine.failed ? ` · ${mine.failed} failed` : ""} · {mine.last_30_days} in the last 30 days</span>
            <span>{mine.last_sent_at ? `Last sent ${when(mine.last_sent_at)}` : "Never sent"}</span>
            <span>{mine.flows.length ? `Sent by flows: ${mine.flows.join(", ")}` : "No flow sends it yet"}</span>
          </>
        ) : <Spinner />}
        <span className="mail-row-hint">From code: <code>{`send_mail(to, template="${tpl.name}", data={…})`}</code></span>
      </div>
    </div>
  );
}

// ── modals ────────────────────────────────────────────────────────────

function lastTestAddress() {
  try { return localStorage.getItem("pawabase.mail.test-to") || ""; } catch { return ""; }
}

export function TestModal({ base, rendered, subject, variables, onClose }) {
  const [to, setTo] = useState(lastTestAddress());
  const [run, busy] = useAction();
  const [result, setResult] = useState(null);
  const send = async () => {
    try { localStorage.setItem("pawabase.mail.test-to", to); } catch { /* private window */ }
    const out = await run(async () => {
      const filled = await post(`${base}/mail/render`, { subject, html: rendered.html, text: rendered.text, data: sampleData(variables) });
      return post(`${base}/mail/test`, { to: to.split(/[\s,;]+/).filter(Boolean), subject: `[Test] ${filled.subject || "(no subject)"}`, html: filled.html, text: filled.text });
    });
    if (out) setResult(out);
  };
  return (
    <Modal title="Send a test email" onClose={onClose} footer={<><Button onClick={onClose}>Close</Button><Button variant="primary" disabled={busy || !to.trim()} onClick={send}>{busy ? "Sending…" : "Send test"}</Button></>}>
      <div className="stack">
        <Field label="To" hint="Your own address, or several separated by commas."><input autoFocus value={to} onChange={(e) => setTo(e.target.value)} placeholder="you@example.com" onKeyDown={(e) => e.key === "Enter" && send()} /></Field>
        <p className="hint">Sent with the sample values from the Values tab. The subject starts with [Test].</p>
        {result && <div className={`alert ${result.suppressed ? "warn" : "ok"}`}>{result.suppressed ? "Recorded but not delivered: no mail server is configured, or delivery is paused." : "Sent. It should arrive within a minute."}</div>}
      </div>
    </Modal>
  );
}

function PublishModal({ checks, busy, onPublish, onClose }) {
  const [note, setNote] = useState("");
  const errors = checks.filter((c) => c.level === "error");
  return (
    <Modal title="Publish this template" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy} onClick={() => onPublish(note)}>{busy ? "Publishing…" : errors.length ? "Publish anyway" : "Publish"}</Button></>}>
      <div className="stack">
        <p>Everything that sends this template — your code, flows and sign-in — uses the published version from now on. The previous one stays in the version history.</p>
        <Field label="What changed" optional><input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="New footer, clearer button…" onKeyDown={(e) => e.key === "Enter" && onPublish(note)} /></Field>
        {errors.length > 0 && <CheckList checks={errors} />}
      </div>
    </Modal>
  );
}

function ImportModal({ editor, onImported, onClose }) {
  const [html, setHtml] = useState("");
  const [mode, setMode] = useState("blocks");
  const load = (file) => file && file.text().then(setHtml);
  const apply = () => {
    const body = (/<body[^>]*>([\s\S]*)<\/body>/i.exec(html) || [null, html])[1];
    if (mode === "blocks") editor.commands.setContent(body, { emitUpdate: true });
    else editor.commands.setContent({ type: "doc", content: [{ type: "htmlBlock", attrs: { html: body } }] }, { emitUpdate: true });
    onImported();
    onClose();
  };
  return (
    <Modal title="Import HTML" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!html.trim()} onClick={apply}>Replace the email</Button></>}>
      <div className="stack">
        <Segmented value={mode} onChange={setMode} options={[["blocks", "Convert to editable blocks"], ["raw", "Keep exactly as HTML"]]} />
        <p className="hint">{mode === "blocks" ? "Headings, text, links, images and lists become blocks you can edit. Complex layouts may need tidying." : "The HTML is kept as one block, sent exactly as written."}</p>
        <label className="btn sm"><Icon name="upload" />Choose a file<input type="file" accept=".html,.htm,text/html" hidden onChange={(e) => load(e.target.files?.[0])} /></label>
        <textarea className="mono" rows={12} value={html} onChange={(e) => setHtml(e.target.value)} placeholder="<html>…</html>" />
        <p className="hint">Undo (⌘Z) brings back what was there before.</p>
      </div>
    </Modal>
  );
}

function ShortcutsModal({ onClose }) {
  const rows = [
    ["⌘ S", "Save now"], ["⌘ ↵", "Send a test"], ["⌘ ⇧ P", "Publish"], ["⌘ ⇧ E", "Switch edit and preview"],
    ["⌘ Z / ⌘ ⇧ Z", "Undo / redo"], ["/", "Insert a block"], ["{{", "Insert a value"], ["⌘ B · ⌘ I · ⌘ U", "Bold · italic · underline"],
    ["⌘ K", "Link selected text"], ["⌘ /", "These shortcuts"],
  ];
  return (
    <Modal title="Keyboard shortcuts" onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      <dl className="mail-shortcuts">{rows.map(([k, label]) => <div key={k}><dt><kbd>{k}</kbd></dt><dd>{label}</dd></div>)}</dl>
    </Modal>
  );
}

function DuplicateModal({ base, body, onClose, onDone }) {
  const current = body();
  const [name, setName] = useState(`${current.name}_copy`);
  const [run, busy] = useAction();
  const go = async () => {
    const draft = current.draft;
    const created = await run(() => post(`${base}/mail-templates`, { ...current, name, status: "draft", favorite: false, subject: "", preheader: "", html: "", text: "", design: null, draft }), "Duplicated");
    if (created) onDone(created.name);
  };
  return (
    <Modal title="Duplicate template" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !TEMPLATE_NAME.test(name)} onClick={go}>Duplicate</Button></>}>
      <Field label="New name" hint="Lowercase letters, numbers, dashes and underscores, starting with a letter."><input className="mono" autoFocus value={name} onChange={(e) => setName(e.target.value)} /></Field>
    </Modal>
  );
}

function SaveBlockModal({ env, kit, content, onClose }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("block");
  const [run, busy] = useAction();
  const save = async () => {
    const next = { ...(kit || {}), blocks: [...(kit?.blocks || []), { id: Date.now().toString(36), name: name.trim(), kind, content }] };
    const ok = await run(() => put(envPath(env, "/mail/kit"), { brand: next.brand || {}, palettes: next.palettes || [], blocks: next.blocks, folders: next.folders || [] }), "Saved to the brand kit");
    if (ok) { kit?.reload?.(); onClose(); }
  };
  return (
    <Modal title="Save as a reusable block" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !name.trim()} onClick={save}>Save</Button></>}>
      <div className="stack">
        <Field label="Name"><input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Support footer" /></Field>
        <Field label="Kind"><Segmented value={kind} onChange={setKind} options={[["block", "Block"], ["header", "Header"], ["footer", "Footer"], ["signature", "Signature"]]} /></Field>
        <p className="hint">Insert it in any template from the / menu or Insert.</p>
      </div>
    </Modal>
  );
}

export { CHECK_TONES };
