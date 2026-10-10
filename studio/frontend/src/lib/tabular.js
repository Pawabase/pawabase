// CSV and JSON in and out of the browser, for importing into and exporting from a resource.

/** Parse delimited text into an array of rows (arrays of strings). Handles quotes, escaped quotes, a BOM and any of , ; or tab. */
export function parseDelimited(text) {
  const source = text.replace(/^﻿/, "");
  const first = source.split(/\r?\n/, 1)[0] || "";
  const delimiter = [",", ";", "\t"].map((d) => [d, first.split(d).length]).sort((a, b) => b[1] - a[1])[0][0];
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];
    if (quoted) {
      if (ch === '"' && source[i + 1] === '"') { cell += '"'; i += 1; } else if (ch === '"') quoted = false; else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delimiter) { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && source[i + 1] === "\n") i += 1;
      row.push(cell); cell = "";
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
    } else cell += ch;
  }
  row.push(cell);
  if (row.some((value) => value !== "")) rows.push(row);
  return rows;
}

/** Read a file the user chose as { columns, rows } where rows are objects keyed by column. */
export function readTable(name, text) {
  if (/\.json$/i.test(name) || /^\s*[[{]/.test(text)) {
    const parsed = JSON.parse(text);
    const list = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.data) ? parsed.data : null;
    if (!list || !list.every((item) => item && typeof item === "object" && !Array.isArray(item))) throw new Error("A JSON file must hold an array of objects.");
    const columns = [...new Set(list.flatMap((item) => Object.keys(item)))];
    return { columns, rows: list };
  }
  const [head, ...body] = parseDelimited(text);
  if (!head) throw new Error("The file is empty.");
  const columns = head.map((name) => name.trim());
  return { columns, rows: body.map((cells) => Object.fromEntries(columns.map((column, i) => [column, cells[i] ?? ""]))) };
}

const NUMBERS = ["integer", "int", "number", "float", "decimal", "bigint"];
const BOOLEANS = ["boolean", "bool"];

/** Turn a cell into what a field of this type accepts. An empty cell is left out. */
export function coerce(value, type) {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string") return value;
  const text = value.trim();
  if (NUMBERS.includes(type)) { const number = Number(text); return text === "" ? undefined : Number.isNaN(number) ? value : number; }
  if (BOOLEANS.includes(type)) return /^(true|1|yes|y|t)$/i.test(text) ? true : /^(false|0|no|n|f)$/i.test(text) ? false : text;
  if (["json", "object", "array"].includes(type)) { try { return JSON.parse(text); } catch { return text; } }
  return value;
}

function quote(value) {
  if (value === null || value === undefined) return "";
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(columns, rows) {
  return [columns.map(quote).join(","), ...rows.map((row) => columns.map((column) => quote(row[column])).join(","))].join("\r\n");
}

export function download(filename, text, type) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([text], { type }));
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}
