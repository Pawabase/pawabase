import { router } from "@inertiajs/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { del, get, patch, post, envPath } from "../lib/api";

const SECTIONS = [
  "overview", "database", "resources", "schemas", "transformers", "policies", "routes", "explorer",
  "functions", "flows", "subscriptions", "schedules", "webhooks", "inbound-hooks", "mail-templates",
  "users", "storage", "realtime", "releases", "jobs", "events", "observability", "keys", "secrets", "settings",
];

const READ_COMMANDS = [
  ["resources", "List resource definitions", "resources"], ["schemas", "List schemas", "schemas"],
  ["transformers", "List transformers", "transformers"], ["policies", "List policies", "policies"],
  ["routes", "List custom routes", "routes"], ["functions", "List functions", "functions"],
  ["subscriptions", "List event subscriptions", "subscriptions"], ["schedules", "List schedules", "schedules"],
  ["webhooks", "List webhooks", "webhooks"], ["inbound-hooks", "List inbound hooks", "inbound-hooks"],
  ["mail-templates", "List mail templates", "mail-templates"], ["storage", "List storage buckets", "buckets"],
  ["keys", "List API keys", "keys"], ["secrets", "List secret names", "secrets"], ["releases", "List releases", "releases"],
  ["versions", "List API versions", "api-versions"], ["deployments", "List deployments", "deployments"],
];

const COMMANDS = [
  ["help", "Show every command and example"], ["clear", "Clear console output"], ["close", "Close the console"],
  ["status", "Show the current environment"], ["blocks", "List available Flow blocks"],
  ["flows", "List flows"], ["flows run <name> [json] --confirm", "Run a Flow now"], ["flows runs [name]", "List recent Flow runs"],
  ["data list <resource> [limit]", "List resource records"], ["data get <resource> <id>", "Read one record"],
  ["data create <resource> <json> --confirm", "Create a record"], ["data update <resource> <id> <json> --confirm", "Update a record"],
  ["data delete <resource> <id> --confirm", "Delete a record"], ["events", "List recent events"],
  ["events emit <name> <json> --confirm", "Emit a platform event"], ["jobs", "List jobs"],
  ["jobs retry <id> --confirm", "Retry a failed job"], ...SECTIONS.map((section) => [`open ${section}`, `Open ${section.replaceAll("-", " ")}`]),
  ...READ_COMMANDS.map(([name, description]) => [name, description]), ["users [search]", "List application users"],
  ["roles", "List application roles"], ["organizations", "List application organizations"],
  ["realtime channels", "List active realtime channels"], ["realtime connections", "List realtime connections"], ["realtime activity", "Show realtime activity"],
];

function parseJson(text, fallback = {}) {
  if (!text?.trim()) return fallback;
  return JSON.parse(text);
}

function commandLine(command) {
  return command.replace(/\s+--confirm$/, "").trim();
}

function scalar(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return Array.isArray(value) ? `[${value.length} items]` : "{…}";
  return String(value);
}

function terminalOutput(value) {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "OK";
  const rows = Array.isArray(value) ? value : value.data;
  if (Array.isArray(rows)) {
    if (!rows.length) return "No results.";
    const columns = [...new Set(rows.flatMap((row) => Object.keys(row || {})))].slice(0, 6);
    const width = Object.fromEntries(columns.map((column) => [column, Math.max(column.length, ...rows.slice(0, 20).map((row) => scalar(row?.[column]).slice(0, 28).length))]));
    const line = (row) => columns.map((column) => scalar(row?.[column]).slice(0, 28).padEnd(width[column])).join("  ");
    return `${line(Object.fromEntries(columns.map((column) => [column, column])))}\n${columns.map((column) => "─".repeat(width[column])).join("  ")}\n${rows.slice(0, 20).map(line).join("\n")}${rows.length > 20 ? `\n… ${rows.length - 20} more result(s)` : ""}`;
  }
  if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${key}: ${scalar(item)}`).join("\n");
  return String(value);
}

const HELP_TEXT = ["PawaBase Console — commands", "", ...COMMANDS.map(([name, description]) => `  ${name.padEnd(44)} ${description}`), "", "Tip: press Tab to complete a command. Add --confirm to commands that run or change state."].join("\n");

export default function Console({ env }) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [lines, setLines] = useState([]);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const inputRef = useRef(null);
  const outputRef = useRef(null);
  const base = env ? envPath(env) : null;
  const suggestions = useMemo(() => {
    const query = input.toLowerCase().trim();
    return COMMANDS.filter(([name, description]) => !query || `${name} ${description}`.toLowerCase().includes(query)).slice(0, 7);
  }, [input]);

  useEffect(() => {
    const onKey = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        setOpen((value) => !value);
      }
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 0); }, [open]);
  useEffect(() => {
    if (open && outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [lines, open]);
  if (!env) return null;

  const append = (command, output, error = false) => setLines((items) => [...items, { command, output: terminalOutput(output), error }]);
  const requireConfirmation = (raw) => {
    if (!raw.includes("--confirm")) throw new Error("This command changes data. Run it again with --confirm.");
  };
  const run = async (raw) => {
    const trimmed = raw.trim();
    if (!trimmed || busy) return;
    setInput("");
    setHistory((items) => [trimmed, ...items.filter((item) => item !== trimmed)].slice(0, 50));
    setHistoryIndex(-1);
    if (trimmed === "clear") { setLines([]); return; }
    if (trimmed === "close") { setOpen(false); return; }
    if (trimmed === "help") { append(trimmed, HELP_TEXT); return; }
    if (trimmed === "status") { append(trimmed, { environment: env }); return; }
    if (["hello", "hi", "hey"].includes(trimmed.toLowerCase())) { append(trimmed, "PawaBase Console ready. Type help to see 42 commands."); return; }
    const clean = commandLine(trimmed);
    const [first, second, third] = clean.split(/\s+/, 3);
    try {
      setBusy(true);
      let output;
      if (first === "open" && SECTIONS.includes(second)) {
        router.visit(second === "overview" ? `/envs/${env}` : `/envs/${env}/${second}`);
        output = { opened: second };
      } else if (clean === "blocks") output = await get("/blocks");
      else if (READ_COMMANDS.some(([name]) => name === clean)) {
        const [, , path] = READ_COMMANDS.find(([name]) => name === clean);
        output = await get(`${base}/${path}`);
      } else if (first === "users") output = await get(`/envs/${env}/users`, { service: "auth", params: second ? { search: second } : {} });
      else if (clean === "roles") output = await get(`/envs/${env}/roles`, { service: "auth" });
      else if (clean === "organizations") output = await get(`/envs/${env}/orgs`, { service: "auth" });
      else if (first === "realtime" && ["channels", "connections", "activity"].includes(second || "activity")) output = await get(`/${env}/${second || "activity"}`, { service: "realtime" });
      else if (clean === "flows") output = await get(`${base}/flows`);
      else if (first === "flows" && second === "runs") output = await get(`${base}/flow-runs`, { params: third ? { flow: third } : {} });
      else if (first === "flows" && second === "run") {
        requireConfirmation(trimmed);
        const rest = clean.replace(/^flows\s+run\s+/, ""); const space = rest.indexOf(" ");
        output = await post(`${base}/flows/${space < 0 ? rest : rest.slice(0, space)}/run`, { input: parseJson(space < 0 ? "" : rest.slice(space + 1)) });
      } else if (first === "data" && second === "list") {
        const [resource, limit] = clean.replace(/^data\s+list\s+/, "").split(/\s+/, 2);
        output = await get(`${base}/resources/${resource}/records`, { params: { per_page: limit || 50 } });
      } else if (first === "data" && second === "get") {
        const [resource, id] = clean.replace(/^data\s+get\s+/, "").split(/\s+/, 2); output = await get(`${base}/resources/${resource}/records/${id}`);
      } else if (first === "data" && ["create", "update", "delete"].includes(second)) {
        requireConfirmation(trimmed);
        const rest = clean.replace(/^data\s+\w+\s+/, ""); const [resource, id, ...json] = rest.split(/\s+/);
        if (second === "create") output = await post(`${base}/resources/${resource}/records`, parseJson([id, ...json].join(" ")));
        if (second === "update") output = await patch(`${base}/resources/${resource}/records/${id}`, parseJson(json.join(" ")));
        if (second === "delete") output = await del(`${base}/resources/${resource}/records/${id}`);
      } else if (clean === "events") output = await get(`${base}/events`);
      else if (first === "events" && second === "emit") {
        requireConfirmation(trimmed); const rest = clean.replace(/^events\s+emit\s+/, ""); const space = rest.indexOf(" ");
        output = await post(`${base}/events`, { name: space < 0 ? rest : rest.slice(0, space), payload: parseJson(space < 0 ? "" : rest.slice(space + 1)) });
      } else if (clean === "jobs") output = await get(`${base}/jobs`);
      else if (first === "jobs" && second === "retry") { requireConfirmation(trimmed); output = await post(`${base}/jobs/${third}/retry`); }
      else throw new Error("Unknown command. Run help to see available commands.");
      append(trimmed, output ?? { ok: true });
    } catch (error) { append(trimmed, error.message || String(error), true); } finally { setBusy(false); }
  };

  return <>
    <button type="button" onClick={() => setOpen((value) => !value)} title="Open terminal (⌘/Ctrl J)" style={{ position: "fixed", right: 22, bottom: 18, zIndex: 35, border: "1px solid #41515d", borderRadius: 7, padding: "8px 12px", background: "#101820", color: "#9bf6c8", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontWeight: 700, boxShadow: "0 6px 20px rgba(0,0,0,.25)" }}>›_ terminal</button>
    {open && <section aria-label="PawaBase terminal" style={{ position: "fixed", zIndex: 36, left: 20, right: 20, bottom: 16, maxWidth: 1100, margin: "auto", border: "1px solid #35434d", borderRadius: 9, overflow: "hidden", background: "#0b1117", color: "#d7e1e8", boxShadow: "0 -10px 48px rgba(0,0,0,.45)", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
      <div className="row" style={{ justifyContent: "space-between", padding: "9px 13px", borderBottom: "1px solid #26323b", background: "#141d25", fontSize: 12 }}><span><i style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: "#ff5f57", marginRight: 6 }} /><i style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: "#ffbd2e", marginRight: 6 }} /><i style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: "#28c840", marginRight: 10 }} />pawabase — {project.ref}/{env}</span><button type="button" onClick={() => setOpen(false)} style={{ border: 0, background: "transparent", color: "#94a3b8", cursor: "pointer" }}>esc</button></div>
      <div ref={outputRef} style={{ minHeight: 260, maxHeight: 360, overflow: "auto", padding: 14, fontSize: 12, lineHeight: 1.55 }}>
        {!lines.length && <pre style={{ margin: 0, color: "#9bf6c8", whiteSpace: "pre-wrap" }}>{"PawaBase Terminal\nType help for commands. Tab completes. Up arrow recalls history.\n\n"}</pre>}
        {lines.map((line, index) => <div key={index} style={{ marginBottom: 14 }}><div style={{ color: "#9bf6c8" }}>pawabase@{env}:~$ <span style={{ color: "#e5edf3" }}>{line.command}</span></div><pre style={{ margin: "3px 0 0", whiteSpace: "pre-wrap", color: line.error ? "#ff8585" : "#c9d7e1" }}>{line.output}</pre></div>)}
      </div>
      <div style={{ borderTop: "1px solid #26323b", padding: "10px 14px", display: "flex", gap: 8, alignItems: "center", color: "#9bf6c8", fontSize: 12 }}><span>pawabase@{env}:~$</span><input ref={inputRef} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") run(input); if (event.key === "ArrowUp") { event.preventDefault(); const next = Math.min(historyIndex + 1, history.length - 1); setHistoryIndex(next); setInput(history[next] || ""); } if (event.key === "Tab") { event.preventDefault(); if (suggestions.length) setInput(suggestions[0][0]); } }} placeholder={busy ? "running…" : "type a command"} style={{ flex: 1, minWidth: 0, border: 0, outline: "none", boxShadow: "none", appearance: "none", WebkitAppearance: "none", padding: 0, margin: 0, borderRadius: 0, background: "transparent", backgroundColor: "transparent", color: "#e5edf3", fontFamily: "inherit", fontSize: 12 }} disabled={busy} /></div>
    </section>}
  </>;
}
