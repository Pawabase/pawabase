// The email blocks the editor knows beyond rich text: variables, images,
// buttons, spacers, columns, sections, conditional and repeated content, and
// raw HTML. Each draws itself on the canvas the way it will look in the email;
// its settings are edited in the inspector beside the canvas.

import { Node, mergeAttributes } from "@tiptap/core";
import HorizontalRule from "@tiptap/extension-horizontal-rule";
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer } from "@tiptap/react";

const data = (attrs) => Object.fromEntries(Object.entries(attrs).map(([k, v]) => [`data-${k}`, v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v)]));
const read = (el, name, fallback = null) => {
  const value = el.getAttribute(`data-${name}`);
  return value === null || value === "" ? fallback : value;
};
const attrsFrom = (defaults) => Object.fromEntries(Object.entries(defaults).map(([key, value]) => [key, { default: value }]));

// ── variable ───────────────────────────────────────────────────────────

function VariableView({ node, selected }) {
  const { name, fallback } = node.attrs;
  return (
    <NodeViewWrapper as="span" className={`mail-var ${selected ? "is-selected" : ""}`} title={fallback ? `Falls back to “${fallback}”` : "A value filled in when the email is sent"}>
      {`{{ ${name} }}`}
    </NodeViewWrapper>
  );
}

export const Variable = Node.create({
  name: "variable",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  addAttributes: () => attrsFrom({ name: "user.name", fallback: "" }),
  parseHTML: () => [{ tag: "span[data-variable]", getAttrs: (el) => ({ name: read(el, "variable"), fallback: read(el, "fallback", "") }) }],
  renderHTML: ({ node }) => ["span", { "data-variable": node.attrs.name, "data-fallback": node.attrs.fallback }, `{{ ${node.attrs.name} }}`],
  renderText: ({ node }) => `{{ ${node.attrs.name} }}`,
  addNodeView: () => ReactNodeViewRenderer(VariableView),
});

// ── image ──────────────────────────────────────────────────────────────

function ImageView({ node, selected, extension }) {
  const a = node.attrs;
  const width = /^\d+$/.test(String(a.width)) ? `${a.width}px` : a.width || "100%";
  return (
    <NodeViewWrapper className={`mail-block mail-image ${selected ? "is-selected" : ""}`} style={{ textAlign: a.align || "center" }} data-drag-handle>
      {a.src ? (
        <img src={a.src} alt={a.alt || ""} draggable={false} style={{ width, maxWidth: "100%", borderRadius: a.radius ? `${a.radius}px` : undefined, display: "inline-block" }} />
      ) : (
        <button type="button" className="mail-image-empty" onClick={() => extension.options.onPickImage?.(node)}>
          Add an image: upload one or paste an address
        </button>
      )}
    </NodeViewWrapper>
  );
}

export const EmailImage = Node.create({
  name: "emailImage",
  group: "block",
  atom: true,
  draggable: true,
  addOptions: () => ({ onPickImage: null }),
  addAttributes: () => attrsFrom({ src: "", alt: "", width: "100%", align: "center", href: "", radius: 0 }),
  parseHTML: () => [
    { tag: "div[data-email-image]", getAttrs: (el) => ({ src: read(el, "src", ""), alt: read(el, "alt", ""), width: read(el, "width", "100%"), align: read(el, "align", "center"), href: read(el, "href", ""), radius: Number(read(el, "radius", 0)) }) },
    { tag: "img[src]", getAttrs: (el) => ({ src: el.getAttribute("src"), alt: el.getAttribute("alt") || "", width: el.getAttribute("width") || "100%" }) },
  ],
  renderHTML: ({ node }) => ["div", { "data-email-image": "", ...data(node.attrs) }],
  addNodeView: () => ReactNodeViewRenderer(ImageView),
});

// ── button ─────────────────────────────────────────────────────────────

function ButtonView({ node, selected, extension }) {
  const a = node.attrs;
  const theme = extension.options.theme?.() || {};
  return (
    <NodeViewWrapper className={`mail-block mail-button ${selected ? "is-selected" : ""}`} style={{ textAlign: a.align || "left" }}>
      <span
        className="mail-button-face"
        style={{
          background: a.background || theme.accent,
          color: a.color || theme.accentText,
          borderRadius: `${a.radius ?? 8}px`,
          padding: `${a.paddingY ?? 13}px ${a.paddingX ?? 24}px`,
          display: a.fullWidth ? "block" : "inline-block",
        }}
      >
        {a.variable ? `{{ ${a.variable} }}` : a.text || "Button"}
      </span>
    </NodeViewWrapper>
  );
}

export const Button = Node.create({
  name: "button",
  group: "block",
  atom: true,
  draggable: true,
  addOptions: () => ({ theme: null }),
  addAttributes: () => attrsFrom({ text: "Get started", href: "https://", variable: "", align: "left", background: "", color: "", radius: 8, paddingX: 24, paddingY: 13, fullWidth: false }),
  parseHTML: () => [{ tag: "div[data-button]", getAttrs: (el) => ({ text: read(el, "text", "Button"), href: read(el, "href", "#"), variable: read(el, "variable", ""), align: read(el, "align", "left"), background: read(el, "background", ""), color: read(el, "color", ""), radius: Number(read(el, "radius", 8)), paddingX: Number(read(el, "paddingX", 24)), paddingY: Number(read(el, "paddingY", 13)), fullWidth: read(el, "fullWidth") === "true" }) }],
  renderHTML: ({ node }) => ["div", { "data-button": "", ...data(node.attrs) }],
  addNodeView: () => ReactNodeViewRenderer(ButtonView),
});

// ── spacer ─────────────────────────────────────────────────────────────

function SpacerView({ node, selected }) {
  return (
    <NodeViewWrapper className={`mail-block mail-spacer ${selected ? "is-selected" : ""}`} style={{ height: Number(node.attrs.height) || 24 }}>
      <span className="mail-spacer-label">{node.attrs.height}px</span>
    </NodeViewWrapper>
  );
}

export const Spacer = Node.create({
  name: "spacer",
  group: "block",
  atom: true,
  draggable: true,
  addAttributes: () => attrsFrom({ height: 24 }),
  parseHTML: () => [{ tag: "div[data-spacer]", getAttrs: (el) => ({ height: Number(read(el, "height", 24)) }) }],
  renderHTML: ({ node }) => ["div", { "data-spacer": "", ...data(node.attrs) }],
  addNodeView: () => ReactNodeViewRenderer(SpacerView),
});

// ── divider: the rule, with a colour, a thickness and room around it ──

export const Divider = HorizontalRule.extend({
  addAttributes: () => attrsFrom({ color: "", thickness: 1, spacing: 24 }),
  parseHTML: () => [{ tag: "hr", getAttrs: (el) => ({ color: read(el, "color", ""), thickness: Number(read(el, "thickness", 1)), spacing: Number(read(el, "spacing", 24)) }) }],
  renderHTML: ({ node }) => ["hr", { ...data(node.attrs), style: `border:0;border-top:${node.attrs.thickness}px solid ${node.attrs.color || "#e7e7ea"};margin:${node.attrs.spacing}px 0` }],
});

// ── columns ────────────────────────────────────────────────────────────

function ColumnsView({ node, selected }) {
  return (
    <NodeViewWrapper className={`mail-block mail-columns ${selected ? "is-selected" : ""}`} style={{ "--gap": `${node.attrs.gap ?? 20}px`, alignItems: node.attrs.verticalAlign === "middle" ? "center" : node.attrs.verticalAlign === "bottom" ? "end" : "start" }}>
      <NodeViewContent className="mail-columns-row" />
    </NodeViewWrapper>
  );
}

export const Columns = Node.create({
  name: "columns",
  group: "block",
  content: "column{2,4}",
  draggable: true,
  isolating: true,
  addAttributes: () => attrsFrom({ gap: 20, verticalAlign: "top" }),
  parseHTML: () => [{ tag: "div[data-columns]", getAttrs: (el) => ({ gap: Number(read(el, "gap", 20)), verticalAlign: read(el, "verticalAlign", "top") }) }],
  renderHTML: ({ node }) => ["div", { "data-columns": "", ...data(node.attrs) }, 0],
  addNodeView: () => ReactNodeViewRenderer(ColumnsView),
});

export const Column = Node.create({
  name: "column",
  content: "block+",
  isolating: true,
  addAttributes: () => attrsFrom({ width: null }),
  parseHTML: () => [{ tag: "div[data-column]", getAttrs: (el) => ({ width: read(el, "width") ? Number(read(el, "width")) : null }) }],
  renderHTML: ({ node }) => ["div", mergeAttributes({ "data-column": "", class: "mail-column" }, data(node.attrs), node.attrs.width ? { style: `flex:0 0 ${node.attrs.width}%` } : {}), 0],
});

// ── section: a coloured panel holding other blocks ────────────────────

function SectionView({ node, selected }) {
  const a = node.attrs;
  return (
    <NodeViewWrapper className={`mail-block mail-section ${selected ? "is-selected" : ""}`} style={{ background: a.background || "#f6f6f8", padding: a.padding ?? 24, borderRadius: a.radius ?? 10, border: a.borderColor ? `1px solid ${a.borderColor}` : undefined, textAlign: a.align || undefined }}>
      <NodeViewContent />
    </NodeViewWrapper>
  );
}

export const Section = Node.create({
  name: "section",
  group: "block",
  content: "block+",
  draggable: true,
  isolating: true,
  addAttributes: () => attrsFrom({ background: "#f6f6f8", padding: 24, radius: 10, borderColor: "", align: "" }),
  parseHTML: () => [{ tag: "div[data-section]", getAttrs: (el) => ({ background: read(el, "background", "#f6f6f8"), padding: Number(read(el, "padding", 24)), radius: Number(read(el, "radius", 10)), borderColor: read(el, "borderColor", ""), align: read(el, "align", "") }) }],
  renderHTML: ({ node }) => ["div", { "data-section": "", ...data(node.attrs) }, 0],
  addNodeView: () => ReactNodeViewRenderer(SectionView),
});

// ── conditional and repeated content ──────────────────────────────────

function LogicView({ node, selected }) {
  const a = node.attrs;
  const label = node.type.name === "conditional"
    ? `${a.mode === "unless" ? "Hide if" : "Show if"} ${a.expression || "…"}`
    : `For each ${a.item || "item"} in ${a.list || "…"}`;
  return (
    <NodeViewWrapper className={`mail-block mail-logic ${selected ? "is-selected" : ""}`}>
      <span className="mail-logic-tag" contentEditable={false}>{label}</span>
      <NodeViewContent />
    </NodeViewWrapper>
  );
}

export const Conditional = Node.create({
  name: "conditional",
  group: "block",
  content: "block+",
  draggable: true,
  addAttributes: () => attrsFrom({ expression: "user.plan == \"pro\"", mode: "if" }),
  parseHTML: () => [{ tag: "div[data-conditional]", getAttrs: (el) => ({ expression: read(el, "expression", ""), mode: read(el, "mode", "if") }) }],
  renderHTML: ({ node }) => ["div", { "data-conditional": "", ...data(node.attrs) }, 0],
  addNodeView: () => ReactNodeViewRenderer(LogicView),
});

export const Repeat = Node.create({
  name: "repeat",
  group: "block",
  content: "block+",
  draggable: true,
  addAttributes: () => attrsFrom({ list: "order.items", item: "item" }),
  parseHTML: () => [{ tag: "div[data-repeat]", getAttrs: (el) => ({ list: read(el, "list", ""), item: read(el, "item", "item") }) }],
  renderHTML: ({ node }) => ["div", { "data-repeat": "", ...data(node.attrs) }, 0],
  addNodeView: () => ReactNodeViewRenderer(LogicView),
});

// ── raw HTML ──────────────────────────────────────────────────────────

function HtmlView({ node, selected }) {
  return (
    <NodeViewWrapper className={`mail-block mail-html ${selected ? "is-selected" : ""}`}>
      <span className="mail-logic-tag" contentEditable={false}>HTML</span>
      {/* Drawn in a sandboxed frame: it is the author's own markup, shown as the email shows it. */}
      <iframe title="HTML block" sandbox="" srcDoc={`<body style="margin:0;font-family:sans-serif">${node.attrs.html || ""}</body>`} />
    </NodeViewWrapper>
  );
}

export const HtmlBlock = Node.create({
  name: "htmlBlock",
  group: "block",
  atom: true,
  draggable: true,
  addAttributes: () => attrsFrom({ html: "<p>Your HTML</p>" }),
  parseHTML: () => [{ tag: "div[data-html-block]", getAttrs: (el) => ({ html: el.getAttribute("data-html") ?? el.innerHTML }) }],
  renderHTML: ({ node }) => ["div", { "data-html-block": "", "data-html": node.attrs.html }],
  addNodeView: () => ReactNodeViewRenderer(HtmlView),
});

export const CUSTOM_NODES = [Variable, EmailImage, Button, Spacer, Divider, Columns, Column, Section, Conditional, Repeat, HtmlBlock];
