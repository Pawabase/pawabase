// The editor's document, rendered as an email.
//
// The output is what Pawabase stores and sends: table-based HTML with inline
// styles (so Gmail, Outlook and Apple Mail agree), a hidden preheader, and a
// plain-text version. Values and logic become Jinja — `{{ user.name }}`,
// `{% if … %}`, `{% for … %}` — which the server renders in its sandbox at send
// time. Text a person typed is never read as Jinja: braces in it are wrapped in
// `{% raw %}`.

export const DEFAULT_THEME = {
  font: "Inter",
  fontSize: 16,
  lineHeight: 1.6,
  text: "#1f2328",
  muted: "#6b7280",
  heading: "#0b0c0e",
  link: "#ff7049",
  accent: "#ff7049",
  accentText: "#ffffff",
  background: "#f4f4f6",
  content: "#ffffff",
  width: 600,
  padding: 44,
  radius: 12,
  border: "#e7e7ea",
};

/** Fonts an email can name. Web fonts load where the client allows it (Apple Mail, iOS, many Android apps) and fall back elsewhere. */
export const FONTS = [
  ["Inter", "Inter, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif", "Inter"],
  ["System", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif", null],
  ["Helvetica", "Helvetica, Arial, sans-serif", null],
  ["Georgia", "Georgia, 'Times New Roman', serif", null],
  ["Verdana", "Verdana, Geneva, sans-serif", null],
  ["Trebuchet", "'Trebuchet MS', Helvetica, sans-serif", null],
  ["Courier", "'Courier New', Courier, monospace", null],
  ["DM Sans", "'DM Sans', Helvetica, Arial, sans-serif", "DM+Sans:wght@400;500;700"],
  ["Plus Jakarta Sans", "'Plus Jakarta Sans', Helvetica, Arial, sans-serif", "Plus+Jakarta+Sans:wght@400;500;700"],
  ["Lora", "Lora, Georgia, serif", "Lora:wght@400;600;700"],
  ["Merriweather", "Merriweather, Georgia, serif", "Merriweather:wght@400;700"],
];

export function fontStack(name) {
  const found = FONTS.find(([label]) => label === name);
  return found ? found[1] : name ? `${name}, Helvetica, Arial, sans-serif` : FONTS[0][1];
}

function googleFont(name) {
  if (name === "Inter") return "Inter:wght@400;500;600;700";
  const found = FONTS.find(([label]) => label === name);
  return found ? found[2] : null;
}

// ── escaping ───────────────────────────────────────────────────────────

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
export const escapeHtml = (value) => String(value ?? "").replace(/[&<>"]/g, (ch) => HTML_ESCAPES[ch]);

/** Typed text, safe for Jinja: braces a person typed are kept literally. */
function literal(value) {
  const text = escapeHtml(value);
  return /\{[{%#]/.test(text) ? `{% raw %}${text}{% endraw %}` : text;
}

/** An attribute value. A `{{ value }}` inside a link or image address is left for Jinja on purpose. */
function attr(value) {
  return String(value ?? "").replace(/[&<>"]/g, (ch) => HTML_ESCAPES[ch]);
}

const VARIABLE = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$/;
export const validVariable = (name) => VARIABLE.test(String(name || ""));

export function variableTag(name, fallback) {
  if (!validVariable(name)) return "";
  // `true`: the fallback also covers an empty value, not only a missing one.
  return fallback ? `{{ ${name} | default(${JSON.stringify(String(fallback))}, true) }}` : `{{ ${name} }}`;
}

// ── inline content ─────────────────────────────────────────────────────

function styleOf(styles) {
  return Object.entries(styles)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}:${v}`)
    .join(";");
}

function renderMarks(html, marks = [], theme) {
  let out = html;
  const textStyle = {};
  for (const mark of marks) {
    const a = mark.attrs || {};
    if (mark.type === "bold") out = `<strong>${out}</strong>`;
    else if (mark.type === "italic") out = `<em>${out}</em>`;
    else if (mark.type === "underline") out = `<u>${out}</u>`;
    else if (mark.type === "strike") out = `<s>${out}</s>`;
    else if (mark.type === "code") out = `<code style="font-family:Menlo,Consolas,monospace;font-size:0.92em;background:#f1f1f3;border-radius:4px;padding:1px 5px">${out}</code>`;
    else if (mark.type === "textStyle") {
      if (a.color) textStyle.color = a.color;
      if (a.fontFamily) textStyle["font-family"] = fontStack(a.fontFamily);
      if (a.fontSize) textStyle["font-size"] = a.fontSize;
      if (a.backgroundColor) textStyle["background-color"] = a.backgroundColor;
    } else if (mark.type === "highlight") textStyle["background-color"] = a.color || "#fff3a3";
  }
  if (Object.keys(textStyle).length) out = `<span style="${styleOf(textStyle)}">${out}</span>`;
  const link = marks.find((m) => m.type === "link");
  if (link) {
    const href = link.attrs?.href || "#";
    out = `<a href="${attr(href)}" target="_blank" rel="noopener" style="color:${theme.link};text-decoration:underline">${out}</a>`;
  }
  return out;
}

function renderInline(nodes = [], theme) {
  return nodes
    .map((node) => {
      if (node.type === "text") return renderMarks(literal(node.text), node.marks, theme);
      if (node.type === "hardBreak") return "<br>";
      if (node.type === "variable") return renderMarks(variableTag(node.attrs?.name, node.attrs?.fallback), node.marks, theme);
      return "";
    })
    .join("");
}

// ── blocks ─────────────────────────────────────────────────────────────

const HEADING_SIZES = { 1: 30, 2: 23, 3: 19 };

function align(node) {
  return node.attrs?.textAlign && node.attrs.textAlign !== "left" ? node.attrs.textAlign : undefined;
}

function block(node, theme, ctx) {
  const a = node.attrs || {};
  switch (node.type) {
    case "paragraph": {
      const inner = renderInline(node.content, theme);
      return `<p style="${styleOf({ margin: "0 0 16px", "text-align": align(node), "line-height": a.lineHeight })}">${inner || "&nbsp;"}</p>`;
    }
    case "heading": {
      const level = Math.min(3, Math.max(1, a.level || 1));
      return `<h${level} style="${styleOf({ margin: level === 1 ? "0 0 18px" : "24px 0 12px", "font-size": `${HEADING_SIZES[level]}px`, "line-height": "1.25", "font-weight": "700", color: theme.heading, "letter-spacing": level === 1 ? "-0.02em" : "-0.01em", "text-align": align(node) })}">${renderInline(node.content, theme)}</h${level}>`;
    }
    case "bulletList":
    case "orderedList": {
      const tag = node.type === "bulletList" ? "ul" : "ol";
      const items = (node.content || []).map((item) => `<li style="margin:0 0 6px">${blocks(item.content, theme, ctx).replace(/<p style="margin:0 0 16px/g, '<p style="margin:0')}</li>`).join("");
      return `<${tag} style="margin:0 0 16px;padding-left:24px">${items}</${tag}>`;
    }
    case "blockquote":
      return `<blockquote style="margin:0 0 16px;padding:4px 0 4px 16px;border-left:3px solid ${theme.border};color:${theme.muted}">${blocks(node.content, theme, ctx)}</blockquote>`;
    case "horizontalRule":
      return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:${a.spacing ?? 24}px 0"><tr><td style="border-top:${a.thickness ?? 1}px solid ${a.color || theme.border};font-size:0;line-height:0">&nbsp;</td></tr></table>`;
    case "spacer":
      return `<div style="height:${Number(a.height) || 24}px;line-height:${Number(a.height) || 24}px;font-size:1px">&nbsp;</div>`;
    case "emailImage": {
      if (!a.src) return "";
      const width = a.width || "100%";
      const px = /^\d+$/.test(String(width)) ? Number(width) : null;
      const img = `<img src="${attr(a.src)}" alt="${attr(a.alt || "")}" ${px ? `width="${px}"` : ""} style="${styleOf({ display: "block", width: px ? `${px}px` : width, "max-width": "100%", height: "auto", border: "0", "border-radius": a.radius ? `${a.radius}px` : undefined, margin: a.align === "center" ? "0 auto" : a.align === "right" ? "0 0 0 auto" : undefined })}">`;
      const linked = a.href ? `<a href="${attr(a.href)}" target="_blank" rel="noopener">${img}</a>` : img;
      return `<div style="margin:0 0 16px;text-align:${a.align || "center"}">${linked}</div>`;
    }
    case "button": {
      const bg = a.background || theme.accent;
      const color = a.color || theme.accentText;
      const radius = a.radius ?? 8;
      const full = Boolean(a.fullWidth);
      const label = a.variable ? variableTag(a.variable) : literal(a.text || "Button");
      const href = a.href || "#";
      return `<table role="presentation" cellpadding="0" cellspacing="0" ${full ? 'width="100%"' : ""} align="${a.align || "left"}" style="margin:8px ${a.align === "center" ? "auto" : a.align === "right" ? "0 8px auto" : "0"} 20px;border-collapse:separate"><tr><td align="center" bgcolor="${attr(bg)}" style="background:${bg};border-radius:${radius}px"><a href="${attr(href)}" target="_blank" rel="noopener" style="display:${full ? "block" : "inline-block"};padding:${a.paddingY ?? 13}px ${a.paddingX ?? 24}px;font-size:15px;font-weight:600;line-height:1.2;color:${color};text-decoration:none;border-radius:${radius}px">${label}</a></td></tr></table><div style="clear:both;font-size:0;line-height:0">&nbsp;</div>`;
    }
    case "columns": {
      const cols = node.content || [];
      const gap = Number(a.gap ?? 20);
      const cells = cols
        .map((col, i) => {
          const width = Number(col.attrs?.width) || Math.floor(100 / cols.length);
          return `<td class="pb-col" valign="${a.verticalAlign || "top"}" width="${width}%" style="width:${width}%;vertical-align:${a.verticalAlign || "top"};padding:0 ${i < cols.length - 1 ? gap / 2 : 0}px 0 ${i > 0 ? gap / 2 : 0}px">${blocks(col.content, theme, ctx)}</td>`;
        })
        .join("");
      return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 8px"><tr>${cells}</tr></table>`;
    }
    case "section": {
      const pad = a.padding ?? 24;
      return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;border-collapse:separate"><tr><td style="${styleOf({ background: a.background || "#f6f6f8", padding: `${pad}px`, "border-radius": `${a.radius ?? 10}px`, border: a.borderColor ? `1px solid ${a.borderColor}` : undefined, "text-align": a.align || undefined })}">${blocks(node.content, theme, ctx)}</td></tr></table>`;
    }
    case "conditional": {
      const expression = String(a.expression || "").trim();
      if (!expression) return blocks(node.content, theme, ctx);
      const test = a.mode === "unless" ? `not (${expression})` : expression;
      return `{% if ${test} %}${blocks(node.content, theme, ctx)}{% endif %}`;
    }
    case "repeat": {
      const list = String(a.list || "").trim();
      const item = validVariable(a.item) ? a.item : "item";
      if (!validVariable(list)) return blocks(node.content, theme, ctx);
      return `{% for ${item} in ${list} %}${blocks(node.content, theme, ctx)}{% endfor %}`;
    }
    case "htmlBlock":
      return a.html || "";
    default:
      return node.content ? blocks(node.content, theme, ctx) : "";
  }
}

function blocks(nodes = [], theme, ctx = {}) {
  return nodes.map((node) => block(node, theme, ctx)).join("");
}

/** The whole email: `{ html, text }` for a document, a subject, a preheader and a theme. */
export function renderEmail({ doc, theme: themeIn, subject = "", preheader = "" }) {
  const theme = { ...DEFAULT_THEME, ...(themeIn || {}) };
  const font = fontStack(theme.font);
  const webFont = googleFont(theme.font);
  const body = blocks(doc?.content || [], theme);
  const hiddenPreheader = preheader
    ? `<div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;mso-hide:all;font-size:1px;line-height:1px;color:${theme.background}">${literal(preheader)}${"&#8199;&#65279;&#847; ".repeat(40)}</div>`
    : "";
  const html = `<!doctype html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no,address=no,email=no,date=no">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>${literal(subject)}</title>
${webFont ? `<link href="https://fonts.googleapis.com/css2?family=${webFont}&display=swap" rel="stylesheet">` : ""}
<style>
body{margin:0;padding:0;width:100%;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}
table{border-collapse:collapse;mso-table-lspace:0;mso-table-rspace:0}
img{border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic}
a{color:${theme.link}}
p,h1,h2,h3,ul,ol,blockquote{font-family:${font}}
@media only screen and (max-width:${theme.width + 40}px){
.pb-container{width:100%!important;max-width:100%!important;border-radius:0!important}
.pb-pad{padding:28px 22px!important}
.pb-col{display:block!important;width:100%!important;max-width:100%!important;padding:0 0 12px!important}
}
</style>
</head>
<body style="margin:0;padding:0;background:${theme.background}">
${hiddenPreheader}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:${theme.background}">
<tr><td align="center" style="padding:32px 12px">
<table role="presentation" class="pb-container" width="${theme.width}" cellpadding="0" cellspacing="0" style="width:${theme.width}px;max-width:${theme.width}px;background:${theme.content};border-radius:${theme.radius}px;border-collapse:separate">
<tr><td class="pb-pad" style="padding:${theme.padding}px ${theme.padding}px ${Math.max(16, theme.padding - 16)}px;font-family:${font};font-size:${theme.fontSize}px;line-height:${theme.lineHeight};color:${theme.text}">
${body}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>`;
  return { html, text: renderText(doc) };
}

// ── plain text ─────────────────────────────────────────────────────────

function inlineText(nodes = []) {
  return nodes
    .map((node) => {
      if (node.type === "text") {
        const raw = /\{[{%#]/.test(node.text) ? `{% raw %}${node.text}{% endraw %}` : node.text;
        const link = (node.marks || []).find((m) => m.type === "link");
        return link?.attrs?.href && link.attrs.href !== node.text ? `${raw} (${link.attrs.href})` : raw;
      }
      if (node.type === "hardBreak") return "\n";
      if (node.type === "variable") return variableTag(node.attrs?.name, node.attrs?.fallback);
      return "";
    })
    .join("");
}

function textBlocks(nodes = [], depth = 0) {
  const out = [];
  for (const node of nodes) {
    const a = node.attrs || {};
    switch (node.type) {
      case "paragraph": out.push(inlineText(node.content)); break;
      case "heading": out.push(inlineText(node.content).toUpperCase() === inlineText(node.content) ? inlineText(node.content) : inlineText(node.content)); break;
      case "bulletList":
      case "orderedList":
        (node.content || []).forEach((item, i) => out.push(`${"  ".repeat(depth)}${node.type === "bulletList" ? "-" : `${i + 1}.`} ${textBlocks(item.content, depth + 1).trim()}`));
        break;
      case "blockquote": out.push(textBlocks(node.content, depth).split("\n").map((l) => `> ${l}`).join("\n")); break;
      case "horizontalRule": out.push("———"); break;
      case "button": out.push(`${a.variable ? variableTag(a.variable) : a.text || "Button"}: ${a.href || ""}`); break;
      case "emailImage": if (a.alt) out.push(`[${a.alt}]`); break;
      case "columns": (node.content || []).forEach((col) => out.push(textBlocks(col.content, depth))); break;
      case "section": out.push(textBlocks(node.content, depth)); break;
      case "conditional": {
        const expression = String(a.expression || "").trim();
        const inner = textBlocks(node.content, depth);
        out.push(expression ? `{% if ${a.mode === "unless" ? `not (${expression})` : expression} %}${inner}{% endif %}` : inner);
        break;
      }
      case "repeat": {
        const list = String(a.list || "").trim();
        const inner = textBlocks(node.content, depth);
        out.push(validVariable(list) ? `{% for ${validVariable(a.item) ? a.item : "item"} in ${list} %}${inner}\n{% endfor %}` : inner);
        break;
      }
      case "htmlBlock": out.push(String(a.html || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()); break;
      default: if (node.content) out.push(textBlocks(node.content, depth));
    }
  }
  return out.filter((s) => s !== undefined).join("\n\n").replace(/\n{3,}/g, "\n\n");
}

export function renderText(doc) {
  return textBlocks(doc?.content || []).trim();
}

// ── reading a document ────────────────────────────────────────────────

/** Every variable a document, subject and preheader read (`user.name`, `order.items`…). */
export function usedVariables(doc, ...strings) {
  const found = new Set();
  const walk = (node) => {
    if (!node) return;
    if (node.type === "variable" && node.attrs?.name) found.add(node.attrs.name);
    if (node.type === "button" && node.attrs?.variable) found.add(node.attrs.variable);
    if (node.type === "repeat" && validVariable(node.attrs?.list)) found.add(node.attrs.list);
    for (const value of [node.attrs?.href, node.attrs?.src]) for (const m of String(value || "").matchAll(/\{\{\s*([A-Za-z_][\w.]*)/g)) found.add(m[1]);
    (node.content || []).forEach(walk);
    (node.marks || []).forEach((mark) => { for (const m of String(mark.attrs?.href || "").matchAll(/\{\{\s*([A-Za-z_][\w.]*)/g)) found.add(m[1]); });
  };
  walk(doc);
  for (const s of strings) for (const m of String(s || "").matchAll(/\{\{\s*([A-Za-z_][\w.]*)/g)) found.add(m[1]);
  return [...found];
}

/** Sample values as the nested object a template reads: `{"user.name": "Ada"}` becomes `{user: {name: "Ada"}}`. JSON samples are parsed. */
export function sampleData(variables = []) {
  const data = {};
  for (const variable of variables) {
    if (!validVariable(variable.name)) continue;
    let value = variable.sample ?? "";
    if (typeof value === "string" && /^\s*[[{]/.test(value)) {
      try { value = JSON.parse(value); } catch { /* keep the text */ }
    } else if (value === "true" || value === "false") value = value === "true";
    const parts = variable.name.split(".");
    let cursor = data;
    parts.forEach((part, i) => {
      if (i === parts.length - 1) cursor[part] = value;
      else cursor = cursor[part] = typeof cursor[part] === "object" && cursor[part] !== null ? cursor[part] : {};
    });
  }
  return data;
}

export const emptyDoc = () => ({ type: "doc", content: [{ type: "paragraph" }] });
