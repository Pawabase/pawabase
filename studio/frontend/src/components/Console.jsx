import { router } from "@inertiajs/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./icons";
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

const newTab = (id) => ({ id, name: `terminal ${id}`, input: "", lines: [], busy: false, history: [], historyIndex: -1 });

export default function Console({ env }) {
  const [open, setOpen] = useState(false);
  const [tabs, setTabs] = useState(() => [newTab(1)]);
  const [active, setActive] = useState(1);
  const [counter, setCounter] = useState(1);
  const [maximized, setMaximized] = useState(false);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [match, setMatch] = useState(0);
  const tab = tabs.find((item) => item.id === active) || tabs[0];
  const patchTab = (id, fn) => setTabs((items) => items.map((item) => (item.id === id ? { ...item, ...fn(item) } : item)));
  const { input, lines, busy, history, historyIndex } = tab;
  const setInput = (value) => patchTab(tab.id, () => ({ input: value }));
  const setHistoryIndex = (value) => patchTab(tab.id, () => ({ historyIndex: value }));
  const addTab = () => {
    const id = counter + 1;
    setCounter(id);
    setTabs((items) => [...items, newTab(id)]);
    setActive(id);
    setOpen(true);
  };
  const closeTab = (id) => {
    if (tabs.length === 1) { setOpen(false); setTabs([newTab(counter + 1)]); setActive(counter + 1); setCounter(counter + 1); return; }
    const rest = tabs.filter((item) => item.id !== id);
    setTabs(rest);
    if (active === id) setActive(rest[rest.length - 1].id);
  };
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

  const requireConfirmation = (raw) => {
    if (!raw.includes("--confirm")) throw new Error("This command changes data. Run it again with --confirm.");
  };
  const run = async (raw) => {
    const trimmed = raw.trim();
    if (!trimmed || busy) return;
    const id = tab.id;
    const append = (command, output, error = false) => patchTab(id, (t) => ({ lines: [...t.lines, { command, output: terminalOutput(output), error }] }));
    patchTab(id, (t) => ({ input: "", historyIndex: -1, history: [trimmed, ...t.history.filter((item) => item !== trimmed)].slice(0, 50) }));
    if (trimmed === "clear") { patchTab(id, () => ({ lines: [] })); return; }
    if (trimmed === "close") { setOpen(false); return; }
    if (trimmed === "help") { append(trimmed, HELP_TEXT); return; }
    if (trimmed === "status") { append(trimmed, { environment: env }); return; }
    if (["hello", "hi", "hey"].includes(trimmed.toLowerCase())) { append(trimmed, "PawaBase Console ready. Type help to see 42 commands."); return; }
    const clean = commandLine(trimmed);
    const [first, second, third] = clean.split(/\s+/, 3);
    try {
      patchTab(id, () => ({ busy: true }));
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
    } catch (error) { append(trimmed, error.message || String(error), true); } finally { patchTab(id, () => ({ busy: false })); }
  };

  const needle = query.trim().toLowerCase();
  const matches = needle ? lines.flatMap((line, index) => [line.command, line.output].some((text) => text.toLowerCase().includes(needle)) ? [index] : []) : [];
  const focusMatch = (next) => {
    if (!matches.length) return;
    const at = (next + matches.length) % matches.length;
    setMatch(at);
    outputRef.current?.querySelector(`[data-line="${matches[at]}"]`)?.scrollIntoView({ block: "center" });
  };
  const mark = (text) => {
    if (!needle) return text;
    const parts = [];
    let rest = text;
    let at = rest.toLowerCase().indexOf(needle);
    while (at >= 0) {
      parts.push(rest.slice(0, at), <mark key={parts.length}>{rest.slice(at, at + needle.length)}</mark>);
      rest = rest.slice(at + needle.length);
      at = rest.toLowerCase().indexOf(needle);
    }
    parts.push(rest);
    return parts;
  };
  return (
    <div className={`dock ${open ? "open" : ""} ${maximized ? "max" : ""}`}>
      {open && (
        <section className="term" aria-label="Pawabase terminal">
          <div className="term-head">
            <div className="term-tabs" role="tablist">
              {tabs.map((item) => (
                <div key={item.id} role="tab" aria-selected={item.id === tab.id} className={`term-tab ${item.id === tab.id ? "on" : ""}`} onClick={() => setActive(item.id)}>
                  <Icon name="terminal" size={13} />
                  <span>{item.name}</span>
                  <button type="button" aria-label={`Close ${item.name}`} onClick={(event) => { event.stopPropagation(); closeTab(item.id); }}><Icon name="x" size={12} /></button>
                </div>
              ))}
              <button type="button" className="term-icon" onClick={addTab} title="New terminal" aria-label="New terminal"><Icon name="plus" size={15} /></button>
            </div>
            <div className="term-tools">
              <button type="button" className={`term-icon ${searching ? "on" : ""}`} onClick={() => { setSearching((value) => !value); setQuery(""); }} title="Search" aria-label="Search terminal"><Icon name="search" size={15} /></button>
              <button type="button" className="term-icon" onClick={() => patchTab(tab.id, () => ({ lines: [] }))} title="Clear" aria-label="Clear terminal"><Icon name="trash" size={15} /></button>
              <button type="button" className="term-icon" onClick={() => setMaximized((value) => !value)} title={maximized ? "Restore size" : "Maximize"} aria-label={maximized ? "Restore size" : "Maximize terminal"}><Icon name={maximized ? "minimize" : "maximize"} size={15} /></button>
              <button type="button" className="term-icon" onClick={() => setOpen(false)} title="Close (Esc)" aria-label="Close terminal"><Icon name="x" size={15} /></button>
            </div>
          </div>
          {searching && (
            <div className="term-find">
              <Icon name="search" size={14} />
              <input autoFocus value={query} onChange={(event) => { setQuery(event.target.value); setMatch(0); }} onKeyDown={(event) => { if (event.key === "Enter") focusMatch(match + (event.shiftKey ? -1 : 1)); if (event.key === "Escape") { setSearching(false); setQuery(""); } }} placeholder="Find in output" spellCheck={false} />
              <span>{needle ? (matches.length ? `${match + 1} of ${matches.length}` : "No results") : ""}</span>
              <button type="button" className="term-icon" onClick={() => focusMatch(match - 1)} aria-label="Previous match"><Icon name="chevronUp" size={14} /></button>
              <button type="button" className="term-icon" onClick={() => focusMatch(match + 1)} aria-label="Next match"><Icon name="chevronDown" size={14} /></button>
            </div>
          )}
          <div ref={outputRef} className="term-out" onClick={() => !window.getSelection()?.toString() && inputRef.current?.focus()}>
            {!lines.length && <pre className="term-hello">{"Pawabase terminal\nType help for commands. Tab completes. Up arrow recalls history.\n\n"}</pre>}
            {lines.map((line, index) => (
              <div key={index} data-line={index} className="term-entry">
                <div className="term-cmd"><span className="term-ps"><b>pawabase</b>@{env}</span><span className="term-path"> ~</span> $ {mark(line.command)}</div>
                <pre className={line.error ? "term-err" : ""}>{mark(line.output)}</pre>
              </div>
            ))}
          </div>
          <div className="term-in">
            <span className="term-ps"><b>pawabase</b>@{env}</span><span className="term-path">~</span><span>$</span>
            <input
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") run(input);
                if (event.key === "ArrowUp") { event.preventDefault(); const next = Math.min(historyIndex + 1, history.length - 1); setHistoryIndex(next); setInput(history[next] || ""); }
                if (event.key === "ArrowDown") { event.preventDefault(); const next = Math.max(historyIndex - 1, -1); setHistoryIndex(next); setInput(next < 0 ? "" : history[next] || ""); }
                if (event.key === "Tab") { event.preventDefault(); if (suggestions.length) setInput(suggestions[0][0]); }
                if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") { event.preventDefault(); setSearching(true); }
              }}
              placeholder={busy ? "running…" : ""}
              disabled={busy}
              spellCheck={false}
              autoComplete="off"
            />
          </div>
        </section>
      )}
      <div className="dockbar">
        <button type="button" className="dock-btn" aria-expanded={open} onClick={() => setOpen((value) => !value)} title="Terminal (⌘/Ctrl J)">
          <Icon name="terminal" size={15} />
          <span>Terminal</span>
          <kbd>⌘J</kbd>
        </button>
        <span className="dock-env">{env}</span>
        <button type="button" className="dock-chevron" onClick={() => setOpen((value) => !value)} aria-label={open ? "Collapse terminal" : "Expand terminal"}>
          <Icon name={open ? "chevronDown" : "chevronUp"} size={15} />
        </button>
      </div>
    </div>
  );
}
