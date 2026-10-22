// The visual email editor: a canvas that looks like the email, a formatting
// bubble on selected text, a drag handle on every block, an insert menu, and an
// inspector for whatever block is selected. Built on TipTap; the document it
// edits is rendered to email HTML by ./render.js.

import { DragHandle } from "@tiptap/extension-drag-handle-react";
import TextAlign from "@tiptap/extension-text-align";
import { BackgroundColor, Color, FontFamily, FontSize, TextStyle } from "@tiptap/extension-text-style";
import { Placeholder } from "@tiptap/extensions";
import { NodeSelection } from "@tiptap/pm/state";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import { BubbleMenu } from "@tiptap/react/menus";
import StarterKit from "@tiptap/starter-kit";
import { useEffect, useRef, useState } from "react";
import { Icon } from "../../../../components/icons";
import { Popover, Segmented, Switch } from "../../../../components/ui";
import { MailContext, SlashCommand, VariableSuggestion, blockItems } from "./menus";
import { Button, CUSTOM_NODES, EmailImage } from "./nodes";
import { DEFAULT_THEME, FONTS, fontStack } from "./render";

export const BASE_SWATCHES = ["#0b0c0e", "#1f2328", "#6b7280", "#ffffff", "#ff7049", "#9a8cff", "#2563eb", "#16a34a", "#dc2626", "#f59e0b"];

// ── the editor ────────────────────────────────────────────────────────

/** Create the editor. `onPickImage` returns a promise of an image address (or null). */
export function useMailEditor({ doc, onChange, theme, onPickImage, placeholder }) {
  const themeRef = useRef(theme);
  themeRef.current = theme;
  const pickRef = useRef(onPickImage);
  pickRef.current = onPickImage;
  const changeRef = useRef(onChange);
  changeRef.current = onChange;
  return useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({
        horizontalRule: false,
        codeBlock: false,
        heading: { levels: [1, 2, 3] },
        link: { openOnClick: false, autolink: true, defaultProtocol: "https" },
      }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      TextStyle,
      Color,
      FontFamily,
      FontSize,
      BackgroundColor,
      Placeholder.configure({
        includeChildren: true,
        placeholder: ({ node }) => (node.type.name === "heading" ? "Heading" : "Write, or type / for blocks and {{ for values"),
      }),
      MailContext,
      SlashCommand,
      VariableSuggestion,
      ...CUSTOM_NODES.map((node) => {
        if (node === EmailImage) {
          return EmailImage.configure({
            onPickImage: async (target) => {
              const url = await pickRef.current?.();
              if (!url) return;
              const editor = target && editorRef.current;
              editor?.state.doc.descendants((n, pos) => {
                if (n === target) editor.chain().command(({ tr }) => { tr.setNodeMarkup(pos, undefined, { ...n.attrs, src: url }); return true; }).run();
              });
            },
          });
        }
        if (node === Button) return Button.configure({ theme: () => themeRef.current });
        return node;
      }),
    ],
    content: doc,
    editorProps: { attributes: { class: "mail-prose", spellcheck: "true" } },
    onUpdate: ({ editor }) => changeRef.current?.(editor.getJSON()),
  }, []);
}
// The image node view needs the editor to write the chosen address back; it is set once the editor exists.
const editorRef = { current: null };

/** Keep the menus' shared state (variables, saved blocks) in step with the page. */
export function useMailContext(editor, { variables, blocks, onCreateVariable }) {
  useEffect(() => {
    if (!editor) return;
    editorRef.current = editor;
    Object.assign(editor.storage.mailContext, { variables: variables || [], blocks: blocks || [], onCreateVariable });
  }, [editor, variables, blocks, onCreateVariable]);
}

// ── selection ─────────────────────────────────────────────────────────

const CONTAINERS = new Set(["section", "columns", "conditional", "repeat"]);
const LABELS = {
  paragraph: "Text", heading: "Heading", bulletList: "List", orderedList: "List", blockquote: "Quote",
  emailImage: "Image", button: "Button", spacer: "Spacer", horizontalRule: "Divider", columns: "Columns",
  column: "Column", section: "Section", conditional: "Show if", repeat: "Repeat", htmlBlock: "HTML", variable: "Variable",
};

/** The block being edited, and the containers around it, innermost first: `[{node, pos}]`. */
function targets(editor) {
  const { selection } = editor.state;
  const out = [];
  if (selection instanceof NodeSelection) out.push({ node: selection.node, pos: selection.from });
  const $from = selection.$from;
  if (!(selection instanceof NodeSelection)) {
    const parent = $from.parent;
    if (parent.type.name === "paragraph" || parent.type.name === "heading") out.push({ node: parent, pos: $from.before($from.depth) });
  }
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    const node = $from.node(depth);
    if (CONTAINERS.has(node.type.name) && !out.some((t) => t.node === node)) out.push({ node, pos: $from.before(depth) });
  }
  return out;
}

function setAttrs(editor, pos, patch) {
  editor.chain().command(({ tr }) => {
    const node = tr.doc.nodeAt(pos);
    if (!node) return false;
    tr.setNodeMarkup(pos, undefined, { ...node.attrs, ...patch });
    return true;
  }).run();
}

function replaceNode(editor, pos, json) {
  editor.chain().command(({ tr, state }) => {
    const node = tr.doc.nodeAt(pos);
    if (!node) return false;
    tr.replaceWith(pos, pos + node.nodeSize, state.schema.nodeFromJSON(json));
    return true;
  }).run();
}

// ── the canvas ────────────────────────────────────────────────────────

/** The email as it will look, at its real width, styled by the theme. */
export function EmailCanvas({ editor, theme: themeIn, palette = [] }) {
  const theme = { ...DEFAULT_THEME, ...(themeIn || {}) };
  if (!editor) return <div className="mail-canvas-loading">Loading the editor…</div>;
  const style = {
    "--mail-bg": theme.background,
    "--mail-content": theme.content,
    "--mail-width": `${theme.width}px`,
    "--mail-pad": `${theme.padding}px`,
    "--mail-radius": `${theme.radius}px`,
    "--mail-font": fontStack(theme.font),
    "--mail-size": `${theme.fontSize}px`,
    "--mail-line": theme.lineHeight,
    "--mail-text": theme.text,
    "--mail-heading": theme.heading,
    "--mail-link": theme.link,
    "--mail-muted": theme.muted,
    "--mail-border": theme.border,
  };
  return (
    <div className="mail-canvas" style={style}>
      <div className="mail-sheet">
        <DragHandle editor={editor} className="mail-drag">
          <span className="mail-grip" title="Drag to move · click to select">⋮⋮</span>
        </DragHandle>
        <EditorContent editor={editor} />
        <FormatBubble editor={editor} palette={palette} />
      </div>
    </div>
  );
}

// ── formatting bubble ─────────────────────────────────────────────────

const SIZES = [["", "Normal"], ["13px", "Small"], ["18px", "Large"], ["22px", "Larger"]];

function FormatBubble({ editor, palette }) {
  const [linking, setLinking] = useState(false);
  const [href, setHref] = useState("");
  const state = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive("bold"), italic: e.isActive("italic"), underline: e.isActive("underline"), strike: e.isActive("strike"),
      link: e.getAttributes("link").href || "", color: e.getAttributes("textStyle").color || "", size: e.getAttributes("textStyle").fontSize || "",
      align: e.isActive({ textAlign: "center" }) ? "center" : e.isActive({ textAlign: "right" }) ? "right" : "left",
    }),
  });
  const chain = () => editor.chain().focus();
  const applyLink = () => {
    if (!href.trim()) chain().extendMarkRange("link").unsetLink().run();
    else chain().extendMarkRange("link").setLink({ href: href.trim() }).run();
    setLinking(false);
  };
  return (
    <BubbleMenu
      editor={editor}
      shouldShow={({ editor: e, state: s }) => !s.selection.empty && !(s.selection instanceof NodeSelection) && e.isEditable}
      options={{ placement: "top", offset: 8 }}
      className="mail-bubble"
    >
      {linking ? (
        <div className="mail-bubble-link">
          <input autoFocus value={href} placeholder="https://… or {{ url }}" onChange={(e) => setHref(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); applyLink(); } if (e.key === "Escape") setLinking(false); }} />
          <button type="button" onClick={applyLink}><Icon name="check" size={14} /></button>
        </div>
      ) : (
        <>
          <button type="button" className={state.bold ? "on" : ""} onClick={() => chain().toggleBold().run()} title="Bold (⌘B)"><b>B</b></button>
          <button type="button" className={state.italic ? "on" : ""} onClick={() => chain().toggleItalic().run()} title="Italic (⌘I)"><i>I</i></button>
          <button type="button" className={state.underline ? "on" : ""} onClick={() => chain().toggleUnderline().run()} title="Underline (⌘U)"><u>U</u></button>
          <button type="button" className={state.strike ? "on" : ""} onClick={() => chain().toggleStrike().run()} title="Strikethrough"><s>S</s></button>
          <span className="mail-bubble-sep" />
          <button type="button" className={state.link ? "on" : ""} onClick={() => { setHref(state.link); setLinking(true); }} title="Link (⌘K)"><Icon name="link" size={14} /></button>
          <Popover align="start" width={196} label="Text colour" trigger={(props) => <button type="button" className="mail-bubble-color" style={{ background: state.color || "currentColor" }} title="Text colour" {...props} />}>
            {({ close }) => (<div className="mail-swatches" onClick={(e) => e.target.closest("button") && close(false)}>
              <button type="button" className="mail-swatch none" onClick={() => chain().unsetColor().run()} title="Default">×</button>
              {[...new Set([...palette, ...BASE_SWATCHES])].map((c) => <button key={c} type="button" className="mail-swatch" style={{ background: c }} title={c} onClick={() => chain().setColor(c).run()} />)}
            </div>)}
          </Popover>
          <select className="mail-bubble-select" value={state.size} onChange={(e) => (e.target.value ? chain().setFontSize(e.target.value).run() : chain().unsetFontSize().run())} title="Size">
            {SIZES.map(([value, label]) => <option key={label} value={value}>{label}</option>)}
          </select>
          <span className="mail-bubble-sep" />
          {["left", "center", "right"].map((a) => (
            <button key={a} type="button" className={state.align === a ? "on" : ""} onClick={() => chain().setTextAlign(a).run()} title={`Align ${a}`}>
              <AlignGlyph align={a} />
            </button>
          ))}
          <span className="mail-bubble-sep" />
          <button type="button" onClick={() => chain().insertContent({ type: "variable", attrs: { name: "user.name" } }).run()} title="Insert a value">{"{}"}</button>
          <button type="button" onClick={() => chain().unsetAllMarks().run()} title="Clear formatting">⌫</button>
        </>
      )}
    </BubbleMenu>
  );
}

function AlignGlyph({ align }) {
  const lines = align === "center" ? [[3, 13], [5, 11], [3, 13]] : align === "right" ? [[3, 13], [7, 13], [3, 13]] : [[3, 13], [3, 9], [3, 13]];
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      {lines.map(([a, b], i) => <path key={i} d={`M${a} ${4 + i * 4}H${b}`} />)}
    </svg>
  );
}

// ── insert menu ───────────────────────────────────────────────────────

export function InsertMenu({ editor, blocks = [] }) {
  if (!editor) return null;
  const items = blockItems(blocks);
  const groups = [...new Set(items.map((i) => i.group))];
  return (
    <Popover align="start" width={300} label="Insert a block" trigger={(props) => <button type="button" className="mail-tool" {...props}><Icon name="plus" size={15} />Insert</button>}>
      {({ close }) => (<div className="mail-insert" onClick={(e) => e.target.closest("button") && close(false)}>
        {groups.map((group) => (
          <div key={group}>
            <div className="mail-menu-group">{group}</div>
            {items.filter((i) => i.group === group).map((item) => (
              <button key={`${group}-${item.title}`} type="button" className="mail-menu-item" onClick={() => item.run(editor.chain().focus()).run()}>
                <span className="mail-menu-glyph">{item.glyph}</span>
                <span className="mail-menu-text"><b>{item.title}</b>{item.description && <small>{item.description}</small>}</span>
              </button>
            ))}
          </div>
        ))}
      </div>)}
    </Popover>
  );
}

// ── inspector ─────────────────────────────────────────────────────────

function Row({ label, children, hint }) {
  return (
    <label className="mail-row">
      <span className="mail-row-label">{label}</span>
      {children}
      {hint && <span className="mail-row-hint">{hint}</span>}
    </label>
  );
}

export function ColorField({ value, onChange, palette = [], allowEmpty = true }) {
  const swatches = [...new Set([...palette, ...BASE_SWATCHES])];
  return (
    <div className="mail-color">
      <input type="color" value={/^#[0-9a-f]{6}$/i.test(value || "") ? value : "#ffffff"} onChange={(e) => onChange(e.target.value)} aria-label="Pick a colour" />
      <input className="mono" value={value || ""} placeholder={allowEmpty ? "Default" : "#ffffff"} onChange={(e) => onChange(e.target.value)} />
      <div className="mail-swatches inline">
        {allowEmpty && <button type="button" className="mail-swatch none" title="Default" onClick={() => onChange("")}>×</button>}
        {swatches.slice(0, 10).map((c) => <button key={c} type="button" className="mail-swatch" style={{ background: c }} title={c} onClick={() => onChange(c)} />)}
      </div>
    </div>
  );
}

function Range({ value, onChange, min, max, step = 1, unit = "px" }) {
  return (
    <div className="mail-range">
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <span>{value}{unit}</span>
    </div>
  );
}

/** Settings for the selected block, and for the containers around it. */
export function BlockInspector({ editor, palette, onPickImage, onSaveBlock, variables = [] }) {
  const list = useEditorState({ editor, selector: ({ editor: e }) => (e ? targets(e).map((t) => ({ type: t.node.type.name, attrs: t.node.attrs, pos: t.pos, childCount: t.node.childCount, level: t.node.attrs?.level })) : []) }) || [];
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [list.length, list[0]?.pos]);
  if (!editor) return null;
  if (!list.length) {
    return (
      <div className="mail-inspector-empty">
        <Icon name="layers" size={20} className="faint" />
        <p>Select a block to change it. Type <kbd>/</kbd> on an empty line to add one, or <kbd>{"{{"}</kbd> for a value.</p>
      </div>
    );
  }
  const target = list[Math.min(index, list.length - 1)];
  const a = target.attrs || {};
  const set = (patch) => setAttrs(editor, target.pos, patch);
  const node = editor.state.doc.nodeAt(target.pos);

  const duplicate = () => editor.chain().command(({ tr }) => { const n = tr.doc.nodeAt(target.pos); if (!n) return false; tr.insert(target.pos + n.nodeSize, n.copy(n.content)); return true; }).run();
  const remove = () => editor.chain().focus().command(({ tr }) => { const n = tr.doc.nodeAt(target.pos); if (!n) return false; tr.delete(target.pos, target.pos + n.nodeSize); return true; }).run();

  return (
    <div className="mail-inspector">
      {list.length > 1 && (
        <div className="mail-crumbs">
          {list.map((t, i) => <button key={t.pos} type="button" className={i === index ? "on" : ""} onClick={() => setIndex(i)}>{LABELS[t.type] || t.type}</button>)}
        </div>
      )}
      <div className="mail-inspector-head">
        <b>{LABELS[target.type] || target.type}</b>
        <span className="row">
          {onSaveBlock && node && <button type="button" className="mail-icon-btn" title="Save as a reusable block" onClick={() => onSaveBlock(node.toJSON())}><Icon name="sparkle" size={14} /></button>}
          <button type="button" className="mail-icon-btn" title="Duplicate" onClick={duplicate}><Icon name="copy" size={14} /></button>
          <button type="button" className="mail-icon-btn danger" title="Delete" onClick={remove}><Icon name="trash" size={14} /></button>
        </span>
      </div>

      {(target.type === "paragraph" || target.type === "heading") && (
        <>
          <Row label="Style">
            <Segmented value={target.type === "heading" ? `h${target.level}` : "p"} onChange={(v) => (v === "p" ? editor.chain().focus().setParagraph().run() : editor.chain().focus().setHeading({ level: Number(v[1]) }).run())} options={[["p", "Text"], ["h1", "H1"], ["h2", "H2"], ["h3", "H3"]]} />
          </Row>
          <Row label="Alignment">
            <Segmented value={a.textAlign || "left"} onChange={(v) => editor.chain().focus().setTextAlign(v).run()} options={[["left", "Left"], ["center", "Center"], ["right", "Right"]]} />
          </Row>
          <Row label="Font">
            <select value={editor.getAttributes("textStyle").fontFamily || ""} onChange={(e) => (e.target.value ? editor.chain().focus().setFontFamily(e.target.value).run() : editor.chain().focus().unsetFontFamily().run())}>
              <option value="">Email default</option>
              {FONTS.map(([name]) => <option key={name} value={name}>{name}</option>)}
            </select>
          </Row>
          <p className="mail-row-hint">Select text to make it bold, link it, or change its colour.</p>
        </>
      )}

      {target.type === "emailImage" && (
        <>
          <Row label="Image">
            <div className="row">
              <input value={a.src} placeholder="https://…" onChange={(e) => set({ src: e.target.value })} />
              <button type="button" className="mail-icon-btn" title="Upload" onClick={async () => { const url = await onPickImage?.(); if (url) set({ src: url }); }}><Icon name="upload" size={14} /></button>
            </div>
          </Row>
          <Row label="Alt text" hint="Read aloud by screen readers, and shown when images are off.">
            <input value={a.alt} placeholder="Describe the image" onChange={(e) => set({ alt: e.target.value })} />
          </Row>
          <Row label="Link"><input value={a.href} placeholder="Optional: https://…" onChange={(e) => set({ href: e.target.value })} /></Row>
          <Row label="Width">
            <Segmented value={String(a.width)} onChange={(v) => set({ width: v })} options={[["100%", "Full"], ["75%", "¾"], ["50%", "½"], ["160", "Logo"]]} />
          </Row>
          <Row label="Alignment"><Segmented value={a.align} onChange={(v) => set({ align: v })} options={[["left", "Left"], ["center", "Center"], ["right", "Right"]]} /></Row>
          <Row label="Corners"><Range value={a.radius || 0} min={0} max={32} onChange={(v) => set({ radius: v })} /></Row>
        </>
      )}

      {target.type === "button" && (
        <>
          <Row label="Label"><input value={a.text} onChange={(e) => set({ text: e.target.value, variable: "" })} placeholder="Get started" /></Row>
          <Row label="Link" hint="A value works here too: {{ verify_url }}">
            <input className="mono" value={a.href} onChange={(e) => set({ href: e.target.value })} placeholder="https://…" />
          </Row>
          {variables.length > 0 && (
            <Row label="Use a value as the link">
              <select value="" onChange={(e) => e.target.value && set({ href: `{{ ${e.target.value} }}` })}>
                <option value="">Choose a value…</option>
                {variables.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
              </select>
            </Row>
          )}
          <Row label="Alignment"><Segmented value={a.align} onChange={(v) => set({ align: v })} options={[["left", "Left"], ["center", "Center"], ["right", "Right"]]} /></Row>
          <Switch checked={Boolean(a.fullWidth)} onChange={(v) => set({ fullWidth: v })} label="Full width" />
          <Row label="Background"><ColorField value={a.background} onChange={(v) => set({ background: v })} palette={palette} /></Row>
          <Row label="Text colour"><ColorField value={a.color} onChange={(v) => set({ color: v })} palette={palette} /></Row>
          <Row label="Corners"><Range value={a.radius ?? 8} min={0} max={28} onChange={(v) => set({ radius: v })} /></Row>
          <Row label="Size"><Segmented value={`${a.paddingY}`} onChange={(v) => set({ paddingY: Number(v), paddingX: Number(v) * 2 })} options={[["9", "Small"], ["13", "Medium"], ["17", "Large"]]} /></Row>
        </>
      )}

      {target.type === "variable" && (
        <>
          <Row label="Value" hint="Dotted names reach into objects: user.name, order.total.">
            <input className="mono" list="mail-variable-names" value={a.name} onChange={(e) => set({ name: e.target.value.trim() })} />
            <datalist id="mail-variable-names">{variables.map((v) => <option key={v.name} value={v.name} />)}</datalist>
          </Row>
          <Row label="When it is empty" hint="Shown instead, so a greeting never reads “Hi ,”.">
            <input value={a.fallback} placeholder="Nothing" onChange={(e) => set({ fallback: e.target.value })} />
          </Row>
        </>
      )}

      {target.type === "spacer" && <Row label="Height"><Range value={a.height} min={4} max={120} step={4} onChange={(v) => set({ height: v })} /></Row>}

      {target.type === "horizontalRule" && (
        <>
          <Row label="Colour"><ColorField value={a.color} onChange={(v) => set({ color: v })} palette={palette} /></Row>
          <Row label="Thickness"><Range value={a.thickness} min={1} max={6} onChange={(v) => set({ thickness: v })} /></Row>
          <Row label="Space around"><Range value={a.spacing} min={0} max={64} step={4} onChange={(v) => set({ spacing: v })} /></Row>
        </>
      )}

      {target.type === "columns" && node && (
        <>
          <Row label="Columns">
            <Segmented value={String(node.childCount)} onChange={(v) => {
              const count = Number(v);
              const json = node.toJSON();
              const cols = json.content;
              if (count > cols.length) while (cols.length < count) cols.push({ type: "column", content: [{ type: "paragraph" }] });
              else { const extra = cols.splice(count).flatMap((c) => c.content || []); cols[count - 1].content = [...(cols[count - 1].content || []), ...extra]; }
              cols.forEach((c) => { c.attrs = { ...(c.attrs || {}), width: null }; });
              replaceNode(editor, target.pos, json);
            }} options={[["2", "2"], ["3", "3"], ["4", "4"]]} />
          </Row>
          {node.childCount === 2 && (
            <Row label="Widths">
              <Segmented value={String(node.child(0).attrs.width || 50)} onChange={(v) => {
                const json = node.toJSON();
                json.content[0].attrs = { width: Number(v) };
                json.content[1].attrs = { width: 100 - Number(v) };
                replaceNode(editor, target.pos, json);
              }} options={[["50", "½ ½"], ["33", "⅓ ⅔"], ["67", "⅔ ⅓"]]} />
            </Row>
          )}
          <Row label="Gap"><Range value={a.gap ?? 20} min={0} max={48} step={4} onChange={(v) => set({ gap: v })} /></Row>
          <Row label="Vertical alignment"><Segmented value={a.verticalAlign} onChange={(v) => set({ verticalAlign: v })} options={[["top", "Top"], ["middle", "Middle"], ["bottom", "Bottom"]]} /></Row>
          <p className="mail-row-hint">Columns sit side by side on wide screens and stack on phones.</p>
        </>
      )}

      {target.type === "section" && (
        <>
          <Row label="Background"><ColorField value={a.background} onChange={(v) => set({ background: v })} palette={palette} allowEmpty={false} /></Row>
          <Row label="Border"><ColorField value={a.borderColor} onChange={(v) => set({ borderColor: v })} palette={palette} /></Row>
          <Row label="Padding"><Range value={a.padding ?? 24} min={0} max={56} step={4} onChange={(v) => set({ padding: v })} /></Row>
          <Row label="Corners"><Range value={a.radius ?? 10} min={0} max={28} onChange={(v) => set({ radius: v })} /></Row>
          <Row label="Alignment"><Segmented value={a.align || "left"} onChange={(v) => set({ align: v === "left" ? "" : v })} options={[["left", "Left"], ["center", "Center"]]} /></Row>
        </>
      )}

      {target.type === "conditional" && (
        <>
          <Row label="When"><Segmented value={a.mode} onChange={(v) => set({ mode: v })} options={[["if", "Show if"], ["unless", "Hide if"]]} /></Row>
          <Row label="Condition" hint={'For example user.plan == "pro", order.total > 100 or trial_days_left'}>
            <input className="mono" value={a.expression} onChange={(e) => set({ expression: e.target.value })} />
          </Row>
          {variables.length > 0 && (
            <Row label="Start from a value">
              <select value="" onChange={(e) => e.target.value && set({ expression: e.target.value })}>
                <option value="">Choose a value…</option>
                {variables.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
              </select>
            </Row>
          )}
        </>
      )}

      {target.type === "repeat" && (
        <>
          <Row label="List" hint="A value holding a list, such as order.items."><input className="mono" value={a.list} onChange={(e) => set({ list: e.target.value })} /></Row>
          <Row label="Name for each item" hint="Inside, use {{ item.name }}, {{ item.price }}…"><input className="mono" value={a.item} onChange={(e) => set({ item: e.target.value })} /></Row>
        </>
      )}

      {target.type === "htmlBlock" && (
        <Row label="HTML" hint="Sent as written. Use inline styles; most email clients ignore <style> in the body.">
          <textarea className="mono" rows={10} value={a.html} onChange={(e) => set({ html: e.target.value })} />
        </Row>
      )}
    </div>
  );
}
