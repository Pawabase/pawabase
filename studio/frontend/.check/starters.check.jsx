import { getSchema } from "@tiptap/core";
import { Node as PMNode } from "@tiptap/pm/model";
import StarterKit from "@tiptap/starter-kit";
import TextAlign from "@tiptap/extension-text-align";
import { TextStyle, Color, FontFamily, FontSize, BackgroundColor } from "@tiptap/extension-text-style";
import { CUSTOM_NODES } from "../src/pages/Env/mail/editor/nodes";
import { STARTERS, BLANK } from "../src/pages/Env/mail/editor/starters";
import { renderEmail, usedVariables, DEFAULT_THEME } from "../src/pages/Env/mail/editor/render";

export function run() {
  const schema = getSchema([StarterKit.configure({ horizontalRule: false, codeBlock: false, heading: { levels: [1, 2, 3] } }), TextAlign.configure({ types: ["heading", "paragraph"] }), TextStyle, Color, FontFamily, FontSize, BackgroundColor, ...CUSTOM_NODES]);
  const out = [];
  for (const s of [BLANK, ...STARTERS]) {
    const issues = [];
    try { PMNode.fromJSON(schema, s.doc).check(); } catch (e) { issues.push("schema: " + e.message); }
    try {
      const { html, text } = renderEmail({ doc: s.doc, theme: DEFAULT_THEME, subject: s.subject, preheader: s.preheader });
      if (!html.includes("</html>")) issues.push("no html");
      if (!text.trim()) issues.push("empty text");
    } catch (e) { issues.push("render: " + e.message); }
    const defined = new Set((s.variables || []).map((v) => v.name));
    const loopItems = new Set(); (function walk(n) { if (n.type === "repeat") loopItems.add(n.attrs?.item || "item"); (n.content || []).forEach(walk); })(s.doc);
    const missing = [...usedVariables(s.doc, s.subject || "", s.preheader || "")].filter((n) => !defined.has(n) && !loopItems.has(n.split(".")[0]) && ![...defined].some((d) => n.startsWith(d + ".")));
    if (missing.length) issues.push("no sample for: " + missing.join(", "));
    out.push([s.key || "blank", issues]);
  }
  return out;
}
