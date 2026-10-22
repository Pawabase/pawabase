// Slash commands (type "/") and variable completion (type "{{").

import { computePosition, flip, offset, shift } from "@floating-ui/dom";
import { Extension } from "@tiptap/core";
import { PluginKey } from "@tiptap/pm/state";
import { ReactRenderer } from "@tiptap/react";
import Suggestion from "@tiptap/suggestion";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { validVariable } from "./render";

/** Shared state the menus read: the template's variables and the brand kit's reusable blocks. */
export const MailContext = Extension.create({
  name: "mailContext",
  addStorage: () => ({ variables: [], blocks: [] }),
});

// ── the floating list ─────────────────────────────────────────────────

function floating(Component) {
  return () => {
    let renderer = null;
    let el = null;
    const place = (props) => {
      const rect = props.clientRect?.();
      if (!rect || !el) return;
      computePosition({ getBoundingClientRect: () => rect }, el, {
        placement: "bottom-start",
        strategy: "fixed",
        middleware: [offset(6), flip(), shift({ padding: 8 })],
      }).then(({ x, y }) => Object.assign(el.style, { left: `${x}px`, top: `${y}px` }));
    };
    return {
      onStart: (props) => {
        renderer = new ReactRenderer(Component, { props, editor: props.editor });
        el = renderer.element;
        Object.assign(el.style, { position: "fixed", zIndex: 90, left: "0px", top: "0px" });
        document.body.appendChild(el);
        place(props);
      },
      onUpdate: (props) => {
        renderer?.updateProps(props);
        place(props);
      },
      onKeyDown: (props) => {
        if (props.event.key === "Escape") {
          el?.remove();
          return true;
        }
        return renderer?.ref?.onKeyDown(props) ?? false;
      },
      onExit: () => {
        el?.remove();
        renderer?.destroy();
        el = null;
        renderer = null;
      },
    };
  };
}

const CommandList = forwardRef(function CommandList({ items, command }, ref) {
  const [active, setActive] = useState(0);
  const list = useRef(null);
  useEffect(() => setActive(0), [items]);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);
  useImperativeHandle(ref, () => ({
    onKeyDown: ({ event }) => {
      if (!items.length) return false;
      if (event.key === "ArrowDown") { setActive((i) => (i + 1) % items.length); return true; }
      if (event.key === "ArrowUp") { setActive((i) => (i - 1 + items.length) % items.length); return true; }
      if (event.key === "Enter" || event.key === "Tab") { command(items[active]); return true; }
      return false;
    },
  }));
  if (!items.length) return <div className="mail-menu"><div className="mail-menu-empty">Nothing matches</div></div>;
  let group = null;
  return (
    <div className="mail-menu" ref={list} role="listbox">
      {items.map((item, i) => {
        const header = item.group !== group ? (group = item.group) : null;
        return (
          <div key={`${item.group}-${item.title}-${i}`}>
            {header && <div className="mail-menu-group">{header}</div>}
            <button type="button" role="option" aria-selected={i === active} data-index={i} className={`mail-menu-item ${i === active ? "is-active" : ""}`} onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); command(item); }}>
              <span className="mail-menu-glyph">{item.glyph}</span>
              <span className="mail-menu-text">
                <b>{item.title}</b>
                {item.description && <small>{item.description}</small>}
              </span>
              {item.hint && <kbd>{item.hint}</kbd>}
            </button>
          </div>
        );
      })}
    </div>
  );
});

// ── slash commands ────────────────────────────────────────────────────

const para = (text) => ({ type: "paragraph", content: text ? [{ type: "text", text }] : undefined });
const column = (text) => ({ type: "column", content: [para(text)] });

export function blockItems(blocks = []) {
  const items = [
    { group: "Text", glyph: "¶", title: "Text", description: "A plain paragraph", run: (e) => e.setNode("paragraph") },
    { group: "Text", glyph: "H1", title: "Heading 1", description: "The email's headline", hint: "#", run: (e) => e.setNode("heading", { level: 1 }) },
    { group: "Text", glyph: "H2", title: "Heading 2", description: "A section title", hint: "##", run: (e) => e.setNode("heading", { level: 2 }) },
    { group: "Text", glyph: "H3", title: "Heading 3", description: "A smaller title", hint: "###", run: (e) => e.setNode("heading", { level: 3 }) },
    { group: "Text", glyph: "•", title: "Bulleted list", description: "A simple list", hint: "-", run: (e) => e.toggleBulletList() },
    { group: "Text", glyph: "1.", title: "Numbered list", description: "Steps in order", hint: "1.", run: (e) => e.toggleOrderedList() },
    { group: "Text", glyph: "❝", title: "Quote", description: "A quotation or callout", hint: ">", run: (e) => e.toggleBlockquote() },
    { group: "Content", glyph: "▣", title: "Image", description: "Upload or link an image", run: (e) => e.insertContent({ type: "emailImage" }) },
    { group: "Content", glyph: "⬭", title: "Button", description: "A call to action", run: (e) => e.insertContent({ type: "button" }) },
    { group: "Content", glyph: "—", title: "Divider", description: "A line between sections", hint: "---", run: (e) => e.setHorizontalRule() },
    { group: "Content", glyph: "↕", title: "Spacer", description: "Empty room", run: (e) => e.insertContent({ type: "spacer" }) },
    { group: "Layout", glyph: "▥", title: "Two columns", description: "Side by side, stacked on phones", run: (e) => e.insertContent({ type: "columns", content: [column("Left"), column("Right")] }) },
    { group: "Layout", glyph: "▦", title: "Three columns", description: "Three across, stacked on phones", run: (e) => e.insertContent({ type: "columns", content: [column("One"), column("Two"), column("Three")] }) },
    { group: "Layout", glyph: "▢", title: "Section", description: "A coloured panel for other blocks", run: (e) => e.insertContent({ type: "section", content: [para("Write here")] }) },
    { group: "Dynamic", glyph: "{}", title: "Variable", description: "A value filled in when sent", hint: "{{", run: (e) => e.insertContent({ type: "variable", attrs: { name: "user.name" } }) },
    { group: "Dynamic", glyph: "?", title: "Show if…", description: "Content only some people see", run: (e) => e.insertContent({ type: "conditional", content: [para("Only shown when the condition is true")] }) },
    { group: "Dynamic", glyph: "↻", title: "Repeat for each…", description: "One copy per item in a list", run: (e) => e.insertContent({ type: "repeat", content: [para("")] }) },
    { group: "Dynamic", glyph: "</>", title: "HTML", description: "Your own markup", run: (e) => e.insertContent({ type: "htmlBlock" }) },
  ];
  for (const saved of blocks) {
    const label = { header: "Header", footer: "Footer", signature: "Signature" }[saved.kind] || "Saved block";
    items.push({ group: "Saved", glyph: "★", title: saved.name, description: label, run: (e) => e.insertContent(saved.content) });
  }
  return items;
}

const matches = (item, query) => {
  const q = query.toLowerCase().trim();
  return !q || item.title.toLowerCase().includes(q) || (item.description || "").toLowerCase().includes(q) || item.group.toLowerCase().startsWith(q);
};

export const SlashCommand = Extension.create({
  name: "slashCommand",
  addProseMirrorPlugins() {
    return [
      Suggestion({
        editor: this.editor,
        pluginKey: new PluginKey("mailSlash"),
        char: "/",
        startOfLine: false,
        allow: ({ state, range }) => {
          const $from = state.doc.resolve(range.from);
          return $from.parent.type.name === "paragraph" || $from.parent.type.name === "heading";
        },
        items: ({ query, editor }) => blockItems(editor.storage.mailContext?.blocks || []).filter((item) => matches(item, query)),
        command: ({ editor, range, props }) => props.run(editor.chain().focus().deleteRange(range)).run(),
        render: floating(CommandList),
      }),
    ];
  },
});

// ── variables ─────────────────────────────────────────────────────────

export const VariableSuggestion = Extension.create({
  name: "variableSuggestion",
  addProseMirrorPlugins() {
    return [
      Suggestion({
        editor: this.editor,
        pluginKey: new PluginKey("mailVariable"),
        char: "{{",
        allowSpaces: false,
        items: ({ query, editor }) => {
          const q = query.replace(/\}+$/, "").trim();
          const known = editor.storage.mailContext?.variables || [];
          const items = known
            .filter((v) => !q || v.name.toLowerCase().includes(q.toLowerCase()))
            .map((v) => ({ group: "Variables", glyph: "{}", title: v.name, description: v.description || (v.sample ? `e.g. ${String(v.sample).slice(0, 40)}` : ""), name: v.name }));
          if (q && validVariable(q) && !known.some((v) => v.name === q)) items.push({ group: "New", glyph: "+", title: q, description: "Use a new variable", name: q, create: true });
          return items.slice(0, 40);
        },
        command: ({ editor, range, props }) => {
          editor.chain().focus().deleteRange(range).insertContent([{ type: "variable", attrs: { name: props.name } }, { type: "text", text: " " }]).run();
          if (props.create) editor.storage.mailContext?.onCreateVariable?.(props.name);
        },
        render: floating(CommandList),
      }),
    ];
  },
});
