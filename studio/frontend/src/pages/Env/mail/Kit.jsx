// The brand kit: the styles new templates start from, colour palettes the
// editor offers, reusable blocks (headers, footers, signatures and any saved
// block), and the folders templates are filed in. Shared by every template in
// the environment.

import { useMemo, useState } from "react";
import { Icon } from "../../../components/icons";
import { Button, Card, EmptyState, Segmented, Spinner, useAction } from "../../../components/ui";
import { envPath, put } from "../../../lib/api";
import { pickAndUpload } from "./api";
import { ColorField } from "./editor/EmailEditor";
import { DEFAULT_THEME, FONTS, renderEmail } from "./editor/render";

const KINDS = [["block", "Block"], ["header", "Header"], ["footer", "Footer"], ["signature", "Signature"]];
const COLORS = [["accent", "Buttons"], ["accentText", "Button text"], ["text", "Text"], ["heading", "Headings"], ["link", "Links"], ["content", "Email background"], ["background", "Page background"], ["border", "Lines"]];

const para = (text, marks) => ({ type: "paragraph", content: text ? [{ type: "text", text, ...(marks ? { marks } : {}) }] : undefined });
const muted = (text) => para(text, [{ type: "textStyle", attrs: { color: "#6b7280", fontSize: "13px" } }]);

/** Ready-made headers, footers and signatures, built from the brand. */
function starterBlock(kind, brand) {
  if (kind === "header") {
    return brand.logo
      ? { type: "emailImage", attrs: { src: brand.logo, alt: brand.name || "Logo", width: "120px", align: "left", href: "", radius: 0 } }
      : para(brand.name || "Your brand", [{ type: "bold" }]);
  }
  if (kind === "footer") {
    return { type: "section", attrs: { background: "", padding: 0, radius: 0, borderColor: "", align: "" }, content: [{ type: "horizontalRule", attrs: { color: "", thickness: 1, spacing: 24 } }, muted(`Sent by ${brand.name || "your company"}${brand.address ? ` · ${brand.address}` : ""}`), muted("You are receiving this because you have an account with us.")] };
  }
  return { type: "section", attrs: { background: "", padding: 0, radius: 0, borderColor: "", align: "" }, content: [para("Thanks,"), para(`The ${brand.name || "team"} team`, [{ type: "bold" }])] };
}

function preview(content, theme) {
  return renderEmail({ doc: { type: "doc", content: [content] }, theme, subject: "", preheader: "" }).html;
}

export function Kit({ env, kit }) {
  const [draft, setDraft] = useState(null);
  const [run, busy] = useAction();
  if (!kit) return <div className="empty"><Spinner /></div>;
  const current = draft || { brand: kit.brand || {}, palettes: kit.palettes || [], blocks: kit.blocks || [], folders: kit.folders || [] };
  const dirty = draft !== null;
  const change = (patch) => setDraft({ ...current, ...patch });
  const save = async (next = current, message = "Brand kit saved") => {
    if (await run(() => put(envPath(env, "/mail/kit"), next), message)) { setDraft(null); kit.reload(); }
  };

  return (
    <div className="stack lg">
      {dirty && (
        <div className="alert info row">
          <span className="grow">You have unsaved changes to the brand kit.</span>
          <Button size="sm" onClick={() => setDraft(null)}>Discard</Button>
          <Button size="sm" variant="primary" disabled={busy} onClick={() => save()}>Save brand kit</Button>
        </div>
      )}
      <Brand brand={current.brand} onChange={(brand) => change({ brand })} env={env} />
      <Palettes palettes={current.palettes} onChange={(palettes) => change({ palettes })} />
      <Blocks blocks={current.blocks} brand={current.brand} onChange={(blocks) => change({ blocks })} />
      <Folders folders={current.folders} onChange={(folders) => change({ folders })} />
    </div>
  );
}

function Brand({ brand, onChange, env }) {
  const theme = { ...DEFAULT_THEME, ...brand };
  const set = (key) => (value) => onChange({ ...brand, [key]: value });
  const sample = useMemo(() => renderEmail({
    theme,
    subject: "",
    preheader: "",
    doc: { type: "doc", content: [
      brand.logo ? { type: "emailImage", attrs: { src: brand.logo, alt: "Logo", width: "120px", align: "left", href: "", radius: 0 } } : para(brand.name || "Your brand", [{ type: "bold" }]),
      { type: "heading", attrs: { level: 1 }, content: [{ type: "text", text: "A heading in your style" }] },
      { type: "paragraph", content: [{ type: "text", text: "Body text reads like this, with " }, { type: "text", text: "a link", marks: [{ type: "link", attrs: { href: "https://example.com" } }] }, { type: "text", text: " in your link colour." }] },
      { type: "button", attrs: { text: "A button", href: "https://example.com", variable: "", align: "left", background: "", color: "", radius: 8, paddingX: 24, paddingY: 13, fullWidth: false } },
      { type: "horizontalRule", attrs: { color: "", thickness: 1, spacing: 24 } },
      muted(`Sent by ${brand.name || "your company"}${brand.address ? ` · ${brand.address}` : ""}`),
    ] },
  }).html, [brand]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Card icon="sparkle" title="Brand" description="New templates and the composer start from these styles. A template can still change its own.">
      <div className="mail-kit-grid">
        <div className="mail-inspector">
          <label className="mail-row"><span className="mail-row-label">Name</span><input value={brand.name || ""} onChange={(e) => set("name")(e.target.value)} placeholder="Northwind" /></label>
          <label className="mail-row"><span className="mail-row-label">Postal address</span><input value={brand.address || ""} onChange={(e) => set("address")(e.target.value)} placeholder="12 Marina Road, Lagos" /><span className="mail-row-hint">Many countries require one in marketing mail. Footers use it.</span></label>
          <div className="mail-row"><span className="mail-row-label">Logo</span>
            <div className="row">
              <input value={brand.logo || ""} onChange={(e) => set("logo")(e.target.value)} placeholder="https://…/logo.png" />
              <button type="button" className="mail-icon-btn" title="Upload" onClick={async () => { const url = await pickAndUpload(env); if (url) set("logo")(url); }}><Icon name="upload" size={14} /></button>
            </div>
          </div>
          <label className="mail-row"><span className="mail-row-label">Font</span>
            <select value={theme.font} onChange={(e) => set("font")(e.target.value)}>{FONTS.map(([n]) => <option key={n} value={n}>{n}</option>)}</select>
          </label>
          <div className="mail-kit-colors">
            {COLORS.map(([key, label]) => (
              <label key={key} className="mail-row"><span className="mail-row-label">{label}</span><ColorField value={theme[key]} onChange={set(key)} allowEmpty={false} /></label>
            ))}
          </div>
          <label className="mail-row"><span className="mail-row-label">Width</span>
            <Segmented value={String(theme.width)} onChange={(v) => set("width")(Number(v))} options={[["520", "Narrow"], ["600", "Standard"], ["680", "Wide"]]} />
          </label>
          <label className="mail-row"><span className="mail-row-label">Corners · {theme.radius}px</span>
            <input type="range" min={0} max={24} value={theme.radius} onChange={(e) => set("radius")(Number(e.target.value))} />
          </label>
        </div>
        <div className="mail-kit-preview"><iframe title="Brand preview" sandbox="" srcDoc={sample} /></div>
      </div>
    </Card>
  );
}

function Palettes({ palettes, onChange }) {
  const update = (i, patch) => onChange(palettes.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  return (
    <Card icon="layers" title="Palettes" description="Colours the editor offers next to every colour field." actions={<Button size="sm" onClick={() => onChange([...palettes, { name: `Palette ${palettes.length + 1}`, colors: ["#ff7049", "#111827"] }])}><Icon name="plus" />Add palette</Button>}>
      {palettes.length === 0 ? <p className="hint">No palettes yet. Your brand colours are always offered.</p> : (
        <div className="stack">
          {palettes.map((p, i) => (
            <div key={i} className="mail-palette">
              <div className="row">
                <input value={p.name} onChange={(e) => update(i, { name: e.target.value })} style={{ maxWidth: 260 }} />
                <span className="grow" />
                <button type="button" className="mail-icon-btn danger" title="Delete palette" onClick={() => onChange(palettes.filter((_, j) => j !== i))}><Icon name="trash" size={13} /></button>
              </div>
              <div className="mail-palette-colors">
                {(p.colors || []).map((c, k) => (
                  <button key={k} type="button" className="mail-swatch" style={{ background: c }} title={`${c} · click to remove`} onClick={() => update(i, { colors: p.colors.filter((_, x) => x !== k) })} />
                ))}
                <input type="color" title="Add a colour" value="#ffffff" onChange={(e) => update(i, { colors: [...(p.colors || []), e.target.value] })} />
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function Blocks({ blocks, brand, onChange }) {
  const theme = { ...DEFAULT_THEME, ...brand };
  const update = (i, patch) => onChange(blocks.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const add = (kind) => onChange([...blocks, { id: Date.now().toString(36), name: KINDS.find(([k]) => k === kind)[1], kind, content: starterBlock(kind, brand) }]);
  return (
    <Card icon="flowBlock" title="Reusable blocks" description="Headers, footers, signatures and any block saved from the editor. Insert them from the Insert menu or by typing / in a template." actions={<div className="row"><Button size="sm" onClick={() => add("header")}><Icon name="plus" />Header</Button><Button size="sm" onClick={() => add("footer")}><Icon name="plus" />Footer</Button><Button size="sm" onClick={() => add("signature")}><Icon name="plus" />Signature</Button></div>}>
      {blocks.length === 0 ? (
        <EmptyState icon="flowBlock" title="No reusable blocks yet">Add a header, footer or signature built from your brand, or select any block in the editor and choose “Save as a reusable block”.</EmptyState>
      ) : (
        <div className="mail-blocks">
          {blocks.map((b, i) => (
            <article key={b.id} className="mail-card">
              <div className="mail-thumb" style={{ height: 150 }}><iframe title={b.name} sandbox="" srcDoc={preview(b.content, theme)} tabIndex={-1} scrolling="no" /></div>
              <div className="mail-card-body">
                <div className="row">
                  <input value={b.name} onChange={(e) => update(i, { name: e.target.value })} aria-label="Block name" />
                  <button type="button" className="mail-icon-btn danger" title="Delete block" onClick={() => onChange(blocks.filter((_, j) => j !== i))}><Icon name="trash" size={13} /></button>
                </div>
                <select value={b.kind || "block"} onChange={(e) => update(i, { kind: e.target.value })} aria-label="Kind">{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
              </div>
            </article>
          ))}
        </div>
      )}
    </Card>
  );
}

function Folders({ folders, onChange }) {
  const [name, setName] = useState("");
  const add = () => { const n = name.trim(); if (n && !folders.includes(n)) onChange([...folders, n]); setName(""); };
  return (
    <Card icon="buckets" title="Folders" description="Group templates in the library. Removing a folder here leaves its templates where they are.">
      <div className="mail-tags">
        {folders.map((f) => <span key={f} className="mail-tag">{f}<button type="button" onClick={() => onChange(folders.filter((x) => x !== f))} aria-label={`Remove ${f}`}>×</button></span>)}
        <input value={name} placeholder="New folder, then Enter" onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} style={{ maxWidth: 260 }} />
      </div>
    </Card>
  );
}
