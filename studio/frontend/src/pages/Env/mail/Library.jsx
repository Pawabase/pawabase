// The template library: every template with a live thumbnail, how often it is
// sent and by which flows; search, filters, folders and favourites; and a
// gallery of prebuilt templates to start from.

import { useMemo, useState } from "react";
import { Icon } from "../../../components/icons";
import { Badge, Button, EmptyState, Field, Modal, Popover, Segmented, useAction, when } from "../../../components/ui";
import { del, envPath, post, put, useApi } from "../../../lib/api";
import { Thumbnail } from "./common";
import { DEFAULT_THEME, renderEmail, sampleData } from "./editor/render";
import { BLANK, CATEGORIES, STARTERS } from "./editor/starters";
import { TEMPLATE_NAME } from "./TemplateWorkspace";

const STATUS_FILTERS = [["all", "All"], ["published", "Published"], ["draft", "Drafts"], ["archived", "Archived"]];
const categoryLabel = (key) => CATEGORIES.find(([k]) => k === key)?.[1] || "";

/** Sample values written into a rendered email, for thumbnails. Logic tags are dropped; the real preview renders on the server. */
function fillSample(html, variables) {
  const lookup = (data, path) => path.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), data);
  const fill = (part, data) => part
    // Loops repeat once per sample item, with the item in scope.
    .replace(/\{%\s*for\s+(\w+)\s+in\s+([\w.]+)\s*%\}([\s\S]*?)\{%\s*endfor\s*%\}/g, (_, item, list, body) => {
      const items = lookup(data, list);
      return Array.isArray(items) ? items.map((value) => fill(body, { ...data, [item]: value })).join("") : "";
    })
    .replace(/\{\{\s*([A-Za-z_][\w.]*)\s*(?:\|\s*default\(("(?:[^"\\]|\\.)*")(?:\s*,\s*true)?\))?\s*\}\}/g, (_, path, fallback) => {
      const value = lookup(data, path);
      if (value !== undefined && value !== "" && typeof value !== "object") return String(value);
      return fallback ? JSON.parse(fallback) : "";
    });
  return fill(html.replace(/\{%\s*raw\s*%\}([\s\S]*?)\{%\s*endraw\s*%\}/g, "$1"), sampleData(variables)).replace(/\{%[^%]*%\}/g, "");
}

/** What a template looks like now: its draft when it has one. */
const currentHtml = (t) => (t.draft?.html || t.html || "");

export function Library({ env, kit, onOpen, startCreating = false }) {
  const base = envPath(env);
  const templates = useApi(`${base}/mail-templates`);
  const usage = useApi(`${base}/mail/templates/usage`);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("");
  const [folder, setFolder] = useState("");
  const [favorites, setFavorites] = useState(false);
  const [sort, setSort] = useState("recent");
  const [creating, setCreating] = useState(startCreating);
  const [modal, setModal] = useState(null);
  const [run] = useAction();

  const rows = templates.data?.data || [];
  const folders = [...new Set([...(kit?.folders || []), ...rows.map((t) => t.folder)].filter(Boolean))].sort();
  const stateOf = (t) => (t.status === "archived" ? "archived" : t.status === "draft" && !t.html ? "draft" : "published");
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = rows.filter((t) => {
      if (status === "all" ? stateOf(t) === "archived" : stateOf(t) !== status) return false;
      if (category && t.category !== category) return false;
      if (folder && t.folder !== folder) return false;
      if (favorites && !t.favorite) return false;
      if (q && ![t.name, t.subject, t.description, t.draft?.subject, ...(t.tags || [])].some((s) => String(s || "").toLowerCase().includes(q))) return false;
      return true;
    });
    return list.sort((a, b) => (sort === "name" ? a.name.localeCompare(b.name) : String(b.updated_at).localeCompare(String(a.updated_at))));
  }, [rows, query, status, category, folder, favorites, sort]);
  const recent = useMemo(() => [...rows].filter((t) => stateOf(t) !== "archived").sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 4), [rows]);

  const save = (t, patch, message) => run(() => put(`${base}/mail-templates/${encodeURIComponent(t.name)}`, { ...t, ...patch }), message).then((ok) => ok && templates.reload());
  const remove = async (t) => {
    const use = usage.data?.data?.[t.name];
    const warning = use?.flows?.length ? `\n\nThese flows send it and will fail: ${use.flows.join(", ")}.` : "";
    if (!confirm(`Delete ${t.name}? Its version history goes too.${warning}`)) return;
    if (await run(() => del(`${base}/mail-templates/${encodeURIComponent(t.name)}`), "Template deleted")) templates.reload();
  };

  return (
    <div className="mail-library">
      {recent.length > 0 && status === "all" && !query && (
        <section className="mail-recent">
          <div className="mail-section-title">Recently edited</div>
          <div className="mail-recent-row">
            {recent.map((t) => (
              <button key={t.name} type="button" className="mail-recent-item" onClick={() => onOpen(t.name)}>
                <Icon name="mail" size={15} />
                <span><b className="mono">{t.name}</b><small>{when(t.updated_at)}</small></span>
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="mail-toolbar">
        <div className="mail-search"><Icon name="search" size={14} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search names, subjects and tags" aria-label="Search templates" /></div>
        <Segmented value={status} onChange={setStatus} options={STATUS_FILTERS} />
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category"><option value="">Every category</option>{CATEGORIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <select value={folder} onChange={(e) => setFolder(e.target.value)} aria-label="Folder"><option value="">Every folder</option>{folders.map((f) => <option key={f} value={f}>{f}</option>)}</select>
        <button type="button" className={`mail-chip ${favorites ? "on" : ""}`} onClick={() => setFavorites((v) => !v)}>★ Favourites</button>
        <span className="grow" />
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort"><option value="recent">Recently edited</option><option value="name">Name</option></select>
        <Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" />New template</Button>
      </div>

      {templates.loading && !templates.data ? <div className="empty">Loading templates…</div> : rows.length === 0 ? (
        <EmptyState icon="mail" title="No templates yet" action={<Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" />New template</Button>}>
          Start from a prebuilt welcome, receipt, password reset and more, then make it yours in the editor.
        </EmptyState>
      ) : shown.length === 0 ? (
        <div className="empty">No template matches.</div>
      ) : (
        <div className="mail-grid">
          {shown.map((t) => {
            const use = usage.data?.data?.[t.name];
            const state = stateOf(t);
            return (
              <article key={t.name} className="mail-card">
                <button type="button" className="mail-card-open" onClick={() => onOpen(t.name)} aria-label={`Open ${t.name}`}>
                  <Thumbnail html={fillSample(currentHtml(t), t.variables || [])} />
                </button>
                <div className="mail-card-body">
                  <div className="row">
                    <b className="mono mail-card-name">{t.name}</b>
                    <button type="button" className={`mail-star ${t.favorite ? "on" : ""}`} title={t.favorite ? "Remove from favourites" : "Add to favourites"} onClick={() => save(t, { favorite: !t.favorite })}>★</button>
                    <Popover align="end" width={220} label="Template actions" trigger={(props) => <button type="button" className="mail-icon-btn" {...props}>⋯</button>}>
                      {({ close }) => (<div className="mail-more" onClick={(e) => e.target.closest("button") && close(false)}>
                        <button type="button" onClick={() => onOpen(t.name)}><Icon name="eye" size={14} />Open</button>
                        <button type="button" onClick={() => setModal({ kind: "duplicate", t })}><Icon name="layers" size={14} />Duplicate</button>
                        <button type="button" onClick={() => setModal({ kind: "rename", t })}><Icon name="braces" size={14} />Rename</button>
                        <button type="button" onClick={() => setModal({ kind: "folder", t })}><Icon name="buckets" size={14} />Move to folder</button>
                        {state === "archived"
                          ? <button type="button" onClick={() => save(t, { status: t.html ? "published" : "draft" }, "Restored from the archive")}><Icon name="refresh" size={14} />Unarchive</button>
                          : <button type="button" onClick={() => save(t, { status: "archived" }, "Archived")}><Icon name="backups" size={14} />Archive</button>}
                        <button type="button" className="danger" onClick={() => remove(t)}><Icon name="trash" size={14} />Delete</button>
                      </div>)}
                    </Popover>
                  </div>
                  <div className="mail-card-subject">{t.draft?.subject || t.subject || <span className="faint">No subject</span>}</div>
                  <div className="mail-card-meta">
                    <Badge tone={state === "published" ? (t.draft ? "yellow" : "green") : state === "draft" ? "yellow" : ""}>{state === "published" ? (t.draft ? "Unpublished changes" : "Published") : state === "draft" ? "Draft" : "Archived"}</Badge>
                    {t.category && <Badge>{categoryLabel(t.category)}</Badge>}
                    {t.folder && <span className="faint">📁 {t.folder}</span>}
                    {(t.tags || []).slice(0, 3).map((tag) => <span key={tag} className="mail-tag small">{tag}</span>)}
                  </div>
                  <div className="mail-card-foot">
                    <span>{use ? `${use.last_30_days} sent in 30 days` : "…"}</span>
                    {use?.flows?.length > 0 && <span title={use.flows.join(", ")}>· {use.flows.length} flow{use.flows.length === 1 ? "" : "s"}</span>}
                    <span className="grow" />
                    <span className="faint">{when(t.updated_at)}</span>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {creating && <NewTemplate env={env} kit={kit} existing={rows.map((t) => t.name)} onClose={() => setCreating(false)} onCreated={(name) => { setCreating(false); templates.reload(); onOpen(name); }} />}
      {modal?.kind === "rename" && <RenameModal base={base} t={modal.t} usage={usage.data?.data?.[modal.t.name]} onClose={() => setModal(null)} onDone={() => { setModal(null); templates.reload(); usage.reload(); }} />}
      {modal?.kind === "duplicate" && <CopyModal base={base} t={modal.t} existing={rows.map((t) => t.name)} onClose={() => setModal(null)} onDone={(name) => { setModal(null); templates.reload(); onOpen(name); }} />}
      {modal?.kind === "folder" && <FolderModal t={modal.t} folders={folders} onClose={() => setModal(null)} onSave={(value) => { save(modal.t, { folder: value }, value ? `Moved to ${value}` : "Removed from its folder"); setModal(null); }} />}
    </div>
  );
}

// ── creating ──────────────────────────────────────────────────────────

function uniqueName(base, existing) {
  let name = base.replace(/[^a-z0-9_-]+/g, "_").replace(/^[^a-z]+/, "") || "template";
  if (!existing.includes(name)) return name;
  let i = 2;
  while (existing.includes(`${name}_${i}`)) i += 1;
  return `${name}_${i}`;
}

function NewTemplate({ env, kit, existing, onClose, onCreated }) {
  const [picked, setPicked] = useState(null);
  const [name, setName] = useState("");
  const [run, busy] = useAction();
  const theme = { ...DEFAULT_THEME, ...(kit?.brand || {}) };
  const options = [BLANK, ...STARTERS];
  const previews = useMemo(() => Object.fromEntries(options.map((s) => [s.key, fillSample(renderEmail({ doc: s.doc, theme, subject: s.subject, preheader: s.preheader }).html, s.variables)])), [kit]); // eslint-disable-line react-hooks/exhaustive-deps
  const choose = (s) => { setPicked(s); setName(uniqueName(s.key === "blank" ? "untitled" : s.key, existing)); };
  const create = async () => {
    const out = renderEmail({ doc: picked.doc, theme, subject: picked.subject, preheader: picked.preheader });
    const created = await run(() => post(envPath(env, "/mail-templates"), {
      name, description: picked.description, status: "draft", subject: "", preheader: "", html: "", text: "", category: picked.category,
      variables: picked.variables,
      draft: { subject: picked.subject, preheader: picked.preheader, html: out.html, text: out.text, design: { doc: picked.doc, theme }, saved_at: new Date().toISOString() },
    }), "Template created");
    if (created) onCreated(created.name);
  };
  return (
    <Modal title={picked ? `New template from “${picked.name}”` : "New template"} onClose={onClose} footer={picked ? <><Button onClick={() => setPicked(null)}>Back</Button><Button variant="primary" disabled={busy || !TEMPLATE_NAME.test(name) || existing.includes(name)} onClick={create}>Create and edit</Button></> : <Button onClick={onClose}>Cancel</Button>}>
      {picked ? (
        <div className="mail-new-confirm">
          <Thumbnail html={previews[picked.key]} height={300} />
          <div className="stack">
            <Field label="Name" hint={existing.includes(name) ? "A template with this name already exists." : "Your code and flows send it by this name."}>
              <input className="mono" autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
            </Field>
            <p className="hint">{picked.description} It starts as a draft: publish it when it's ready to send.</p>
          </div>
        </div>
      ) : (
        <div className="mail-gallery">
          {options.map((s) => (
            <button key={s.key} type="button" className="mail-gallery-item" onClick={() => choose(s)}>
              <Thumbnail html={previews[s.key]} height={170} />
              <b>{s.name}</b>
              <small>{s.description}</small>
            </button>
          ))}
        </div>
      )}
    </Modal>
  );
}

function RenameModal({ base, t, usage, onClose, onDone }) {
  const [name, setName] = useState(t.name);
  const [run, busy] = useAction();
  const [flows, setFlows] = useState(null);
  const go = async () => {
    const out = await run(() => post(`${base}/mail-templates/${encodeURIComponent(t.name)}/rename`, { to: name }), "Renamed");
    if (!out) return;
    if (out.flows_to_update?.length) setFlows(out.flows_to_update);
    else onDone();
  };
  if (flows) {
    return (
      <Modal title="Renamed" onClose={onDone} footer={<Button variant="primary" onClick={onDone}>Done</Button>}>
        <p>These flows still send <code>{t.name}</code>. Open each and pick <code>{name}</code> in its Send email step: {flows.join(", ")}.</p>
      </Modal>
    );
  }
  return (
    <Modal title={`Rename ${t.name}`} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !TEMPLATE_NAME.test(name) || name === t.name} onClick={go}>Rename</Button></>}>
      <div className="stack">
        <Field label="New name"><input className="mono" autoFocus value={name} onChange={(e) => setName(e.target.value)} /></Field>
        {usage?.flows?.length > 0 && <div className="alert warn">Sent by {usage.flows.join(", ")}. Update those flows after renaming.</div>}
        <p className="hint">Code that sends this template by name must use the new name too.</p>
      </div>
    </Modal>
  );
}

function CopyModal({ base, t, existing, onClose, onDone }) {
  const [name, setName] = useState(uniqueName(`${t.name}_copy`, existing));
  const [run, busy] = useAction();
  const go = async () => {
    const draft = t.draft || { subject: t.subject, preheader: t.preheader, html: t.html, text: t.text, design: t.design, saved_at: new Date().toISOString() };
    const created = await run(() => post(`${base}/mail-templates`, { ...t, name, status: "draft", favorite: false, subject: "", preheader: "", html: "", text: "", design: null, draft }), "Duplicated");
    if (created) onDone(created.name);
  };
  return (
    <Modal title={`Duplicate ${t.name}`} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !TEMPLATE_NAME.test(name) || existing.includes(name)} onClick={go}>Duplicate</Button></>}>
      <Field label="Name of the copy" hint="The copy starts as a draft."><input className="mono" autoFocus value={name} onChange={(e) => setName(e.target.value)} /></Field>
    </Modal>
  );
}

function FolderModal({ t, folders, onClose, onSave }) {
  const [value, setValue] = useState(t.folder || "");
  return (
    <Modal title={`Move ${t.name}`} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={() => onSave(value.trim())}>Move</Button></>}>
      <div className="stack">
        <Field label="Folder" hint="Pick one, type a new name, or leave empty for no folder.">
          <input list="mail-folder-list" autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder="No folder" />
          <datalist id="mail-folder-list">{folders.map((f) => <option key={f} value={f} />)}</datalist>
        </Field>
      </div>
    </Modal>
  );
}
