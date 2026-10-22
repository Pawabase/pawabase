// What to fix before an email goes out: content mistakes, accessibility,
// deliverability, and how the features it uses behave in each mail client.
// Every check reads the real document and theme; nothing here is a guess
// about an email it has not seen.

import { DEFAULT_THEME, FONTS, usedVariables, validVariable } from "./render";

function hex(color) {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(color || "").trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].split("").map((c) => c + c).join("") : m[1];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two colours, or null when either is not a hex colour. */
export function contrast(a, b) {
  const x = hex(a);
  const y = hex(b);
  if (!x || !y) return null;
  const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
}

function walk(node, visit, parents = []) {
  if (!node) return;
  visit(node, parents);
  (node.content || []).forEach((child) => walk(child, visit, [...parents, node]));
}

const textOf = (node) => (node.content || []).map((c) => (c.type === "text" ? c.text : c.type === "variable" ? `{{${c.attrs?.name}}}` : textOf(c))).join("");
const GENERIC = /^(click here|here|read more|more|link|this|learn more)$/i;

export function checkEmail({ doc, theme: themeIn, subject, preheader, variables = [], html = "" }) {
  const theme = { ...DEFAULT_THEME, ...(themeIn || {}) };
  const out = [];
  const add = (group, level, message, detail) => out.push({ group, level, message, detail });

  // ── content ──
  if (!String(subject || "").trim()) add("Content", "error", "The email has no subject.", "Inboxes show “(no subject)”, and many people delete those unread.");
  else if (String(subject).length > 78) add("Content", "warn", `The subject is ${String(subject).length} characters.`, "Phones show about 40 and desktop inboxes about 70; put what matters first.");
  if (!String(preheader || "").trim()) add("Content", "warn", "There is no preview text.", "Inboxes then show the first words of the body, often “View in browser” or an image's alt text.");
  const blocks = doc?.content || [];
  if (!blocks.length || (blocks.length === 1 && !blocks[0].content)) add("Content", "error", "The email is empty.");

  const used = usedVariables(doc, subject, preheader);
  const defined = new Set(variables.map((v) => v.name));
  const roots = new Set(variables.map((v) => v.name.split(".")[0]));
  const loopNames = new Set();
  walk(doc, (node) => { if (node.type === "repeat" && validVariable(node.attrs?.item)) loopNames.add(node.attrs.item); });
  for (const name of used) {
    if (defined.has(name) || loopNames.has(name.split(".")[0])) continue;
    if ([...defined].some((d) => d.startsWith(`${name}.`) || name.startsWith(`${d}.`)) || roots.has(name)) continue;
    add("Content", "warn", `{{ ${name} }} has no sample value.`, "Define it under Variables so previews and test sends show a real value.");
  }
  for (const v of variables) {
    if (!validVariable(v.name)) add("Content", "error", `“${v.name}” is not a valid variable name.`, "Use letters, numbers and underscores, with dots between parts: user.first_name.");
    else if (!used.some((u) => u === v.name || u.startsWith(`${v.name}.`) || v.name.startsWith(`${u}.`))) add("Content", "info", `{{ ${v.name} }} is defined but not used.`);
  }

  let images = 0;
  let words = 0;
  let headings = [];
  walk(doc, (node) => {
    const a = node.attrs || {};
    if (node.type === "paragraph" || node.type === "heading") words += textOf(node).split(/\s+/).filter(Boolean).length;
    if (node.type === "heading") headings.push(a.level);
    if (node.type === "button") {
      if (!a.href || a.href === "#" || a.href === "https://") add("Content", "error", `The button “${a.text || "Button"}” has no link.`);
      else if (/^http:\/\//i.test(a.href)) add("Deliverability", "warn", `The button “${a.text}” links over http.`, "Use https: some clients warn about insecure links and spam filters score them.");
      const ratio = contrast(a.color || theme.accentText, a.background || theme.accent);
      if (ratio !== null && ratio < 4.5) add("Accessibility", "warn", `The button “${a.text || "Button"}” has low contrast (${ratio.toFixed(1)}:1).`, "Aim for at least 4.5:1 between its text and background.");
      if (GENERIC.test(String(a.text || "").trim())) add("Accessibility", "warn", `The button text “${a.text}” does not say where it goes.`, "Screen reader users often move from link to link: name the action, such as “Reset your password”.");
    }
    if (node.type === "emailImage") {
      images += 1;
      if (!a.src) add("Content", "error", "An image has no address.");
      else if (/^http:\/\//i.test(a.src)) add("Deliverability", "warn", "An image is served over http.", "Gmail and others block or warn about insecure images.");
      if (!String(a.alt || "").trim()) add("Accessibility", "warn", "An image has no alt text.", "Describe it, or say what it shows; it is read aloud and shown when images are blocked.");
    }
    if (node.type === "conditional" && !String(a.expression || "").trim()) add("Content", "warn", "A “Show if” block has no condition, so everyone sees it.");
    if (node.type === "repeat" && !validVariable(a.list)) add("Content", "error", "A “Repeat for each” block has no list to repeat.");
    for (const mark of node.marks || []) {
      if (mark.type !== "link") continue;
      const href = mark.attrs?.href || "";
      if (!href || href === "#") add("Content", "error", `The link “${node.text}” goes nowhere.`);
      else if (/^http:\/\//i.test(href)) add("Deliverability", "warn", `The link “${node.text}” uses http.`);
      if (GENERIC.test(String(node.text || "").trim())) add("Accessibility", "warn", `The link text “${node.text}” does not say where it goes.`);
    }
  });
  headings.forEach((level, i) => {
    if (i > 0 && level > headings[i - 1] + 1) add("Accessibility", "info", `A heading jumps from level ${headings[i - 1]} to ${level}.`, "Screen readers use headings to move around; keep the levels in order.");
  });
  if (headings.filter((l) => l === 1).length > 1) add("Accessibility", "info", "There is more than one main heading (H1).");

  const textRatio = contrast(theme.text, theme.content);
  if (textRatio !== null && textRatio < 4.5) add("Accessibility", "warn", `Body text has low contrast (${textRatio.toFixed(1)}:1).`, "Aim for at least 4.5:1.");
  const linkRatio = contrast(theme.link, theme.content);
  if (linkRatio !== null && linkRatio < 3) add("Accessibility", "warn", `Links are hard to tell from the background (${linkRatio.toFixed(1)}:1).`);
  if (Number(theme.fontSize) < 14) add("Accessibility", "warn", `Body text is ${theme.fontSize}px.`, "Under 14px is hard to read on phones; 16px is the usual size.");
  if (images > 0 && words < images * 20) add("Deliverability", "info", "The email is mostly images.", "Spam filters look at the ratio of text to images, and many people read with images off.");

  const bytes = new Blob([html || ""]).size;
  if (bytes > 102 * 1024) add("Deliverability", "warn", `The HTML is ${Math.round(bytes / 1024)} KB.`, "Gmail cuts messages over 102 KB behind “View entire message”.");

  // ── how clients render what this email uses ──
  const usesRadius = theme.radius > 0 || JSON.stringify(doc || {}).includes('"radius":') && /"radius":[1-9]/.test(JSON.stringify(doc || {}));
  const usesColumns = JSON.stringify(doc || {}).includes('"type":"columns"');
  const usesHtml = JSON.stringify(doc || {}).includes('"type":"htmlBlock"');
  const webFont = FONTS.find(([name]) => name === theme.font)?.[2] || theme.font === "Inter";
  if (usesRadius) add("Compatibility", "info", "Rounded corners show square in Outlook for Windows.", "Apple Mail, Gmail, Outlook.com and the Outlook apps for phones show them rounded.");
  if (webFont) add("Compatibility", "info", `${theme.font} loads in Apple Mail, iOS Mail and Outlook for Mac.`, "Gmail and Outlook for Windows use the fallback fonts instead, so the email still reads well.");
  if (usesColumns) add("Compatibility", "info", "Columns stack on phones in Apple Mail, Gmail and Outlook apps.", "Outlook for Windows keeps them side by side, which is fine at its desktop width.");
  if (usesHtml) add("Compatibility", "warn", "This email contains your own HTML.", "Send a test to each client you care about: custom markup is not checked here.");
  return out;
}

export const LEVEL_ORDER = { error: 0, warn: 1, info: 2 };
