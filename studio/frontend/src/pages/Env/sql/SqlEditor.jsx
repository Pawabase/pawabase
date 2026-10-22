// The SQL editor: a schema browser, an editor that completes table and column names, saved queries shared with the
// team, a per-person history, EXPLAIN, and results you can copy or download.

import { router } from "@inertiajs/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import SqlBuilder from "../../../components/SqlBuilder";
import { Icon } from "../../../components/icons";
import { Badge, Button, Card, Field, Modal, Segmented, Table, formatCell, useAction, useToast, when } from "../../../components/ui";
import { del, get, post, put, useApi } from "../../../lib/api";
import "./sql.css";

const KEYWORDS = [
  "SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "LIMIT", "OFFSET", "JOIN", "LEFT JOIN", "INNER JOIN", "ON", "AND", "OR", "NOT",
  "AS", "DISTINCT", "COUNT(*)", "SUM(", "AVG(", "MIN(", "MAX(", "IN", "LIKE", "IS NULL", "IS NOT NULL", "BETWEEN", "HAVING", "CASE WHEN",
  "INSERT INTO", "UPDATE", "SET", "DELETE FROM", "VALUES", "DESC", "ASC", "UNION ALL", "EXISTS",
];
const AFTER_TABLE = /\b(from|join|into|update|table)\s+$/i;
const SHOWN = 500;

const quoteIdent = (dialect, name) => (/^[a-z_][a-z0-9_]*$/.test(name) ? name : dialect === "mysql" ? `\`${name}\`` : `"${name}"`);

function historyKey(env) { return `pawabase.sql.history.${env}`; }
function readHistory(env) {
  try { return JSON.parse(localStorage.getItem(historyKey(env)) || "[]"); } catch { return []; }
}
function writeHistory(env, entries) {
  try { localStorage.setItem(historyKey(env), JSON.stringify(entries.slice(0, 40))); } catch { /* private mode: history just isn't kept */ }
}

function csvOf(columns, rows) {
  const cell = (v) => {
    if (v === null || v === undefined) return "";
    const text = typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [columns.map(cell).join(","), ...rows.map((row) => columns.map((c) => cell(row[c])).join(","))].join("\n");
}

function download(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}

/** The word being typed at the caret, and the text before it. */
function wordAt(text, caret) {
  const before = text.slice(0, caret);
  const match = /[A-Za-z_][\w$]*$/.exec(before);
  const word = match ? match[0] : "";
  return { word, start: caret - word.length, before: before.slice(0, caret - word.length) };
}

export default function SqlEditor({ env, base, tables, dialect }) {
  const toast = useToast();
  const [run] = useAction();
  const [busy, setBusy] = useState(false);
  const [sql, setSql] = useState("SELECT 1 AS ok");
  const [allowWrite, setAllowWrite] = useState(false);
  const [builder, setBuilder] = useState(false);
  const [analyze, setAnalyze] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [side, setSide] = useState("schema");
  const [history, setHistory] = useState(() => readHistory(env));
  const [active, setActive] = useState(null); // the saved query loaded in the editor
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState("");
  const [shown, setShown] = useState(SHOWN);
  const [caret, setCaret] = useState(0);
  const [pick, setPick] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const area = useRef(null);
  const saved = useApi(`${base}/queries`);

  // ── schema: columns are fetched when a table is opened or used in the query ──
  const [columns, setColumns] = useState({});
  const asked = useRef(new Set());
  const columnsOf = useCallback((table) => {
    if (asked.current.has(table)) return;
    asked.current.add(table);
    get(`${base}/tables/${encodeURIComponent(table)}`)
      .then((info) => setColumns((c) => ({ ...c, [table]: info.columns || [] })))
      .catch(() => setColumns((c) => ({ ...c, [table]: [] })));
  }, [base]);
  const [open, setOpen] = useState({});
  const names = useMemo(() => tables.map((t) => t.name), [tables]);
  useEffect(() => {
    // Tables the query names get their columns loaded, so completion knows them.
    const used = [...sql.matchAll(/\b(?:from|join|update|into)\s+["`]?([A-Za-z_][\w$]*)/gi)].map((m) => m[1]);
    used.filter((t) => names.includes(t)).forEach(columnsOf);
  }, [sql, names, columnsOf]);

  // ── completion ──
  const suggestions = useMemo(() => {
    const { word, before } = wordAt(sql, caret);
    if (dismissed) return [];
    const dotted = /([A-Za-z_][\w$]*)\.$/.exec(before);
    if (!word && !dotted) return [];
    if (!dotted && word.length < 2) return [];
    const lower = word.toLowerCase();
    const starts = (s) => s.toLowerCase().startsWith(lower) && s.toLowerCase() !== lower;
    if (dotted) return (columns[dotted[1]] || []).map((c) => c.name).filter((n) => !word || starts(n)).slice(0, 10).map((v) => ({ value: v, kind: "column" }));
    const used = [...new Set([...sql.matchAll(/\b(?:from|join|update|into)\s+["`]?([A-Za-z_][\w$]*)/gi)].map((m) => m[1]))];
    const cols = used.flatMap((t) => (columns[t] || []).map((c) => ({ value: c.name, kind: "column", note: t })));
    const tabs = names.map((value) => ({ value, kind: "table" }));
    const keys = KEYWORDS.map((value) => ({ value, kind: "keyword" }));
    const pool = AFTER_TABLE.test(before) ? tabs : [...cols, ...tabs, ...keys];
    const seen = new Set();
    return pool.filter((s) => starts(s.value) && !seen.has(s.value) && seen.add(s.value)).slice(0, 8);
  }, [sql, caret, columns, names, dismissed]);
  useEffect(() => { setPick(0); }, [suggestions.length]);

  const insert = (text, replaceWord = false) => {
    const el = area.current;
    const start = el ? el.selectionStart : sql.length;
    const end = el ? el.selectionEnd : sql.length;
    const from = replaceWord ? wordAt(sql, start).start : start;
    const next = sql.slice(0, from) + text + sql.slice(end);
    setSql(next);
    setDismissed(false);
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      const at = from + text.length;
      el.setSelectionRange(at, at);
      setCaret(at);
    });
  };

  // ── running ──
  const statement = () => {
    const el = area.current;
    const selected = el && el.selectionStart !== el.selectionEnd ? sql.slice(el.selectionStart, el.selectionEnd) : sql;
    return selected.trim().replace(/;\s*$/, "");
  };
  const remember = (entry) => {
    const next = [entry, ...history.filter((h) => h.sql !== entry.sql)].slice(0, 40);
    setHistory(next);
    writeHistory(env, next);
  };
  const execute = async (text = statement(), explain = false) => {
    if (!text) return;
    const started = performance.now();
    const prefix = !explain ? "" : dialect === "sqlite" ? "EXPLAIN QUERY PLAN " : analyze ? "EXPLAIN ANALYZE " : "EXPLAIN ";
    setBusy(true);
    setError(null);
    try {
      const answer = await post(`${base}/query`, { sql: prefix + text, allow_write: allowWrite && !explain });
      const ms = Math.round(performance.now() - started);
      setResult({ ...answer, ms, explain, sql: prefix + text });
      setShown(SHOWN);
      remember({ sql: text, at: new Date().toISOString(), ms, rows: answer.rows?.length ?? null, affected: answer.affected ?? null });
    } catch (e) {
      setResult(null);
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key === "Enter") { e.preventDefault(); execute(); return; }
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); setSaving(true); return; }
    if (suggestions.length) {
      if (e.key === "Tab") { e.preventDefault(); insert(suggestions[pick].value, true); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); setPick((p) => (p + 1) % suggestions.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setPick((p) => (p - 1 + suggestions.length) % suggestions.length); return; }
      if (e.key === "Escape") { e.preventDefault(); setDismissed(true); }
    }
  };

  // ── saved queries ──
  const load = (query) => { setSql(query.sql); setActive(query); setResult(null); setError(null); };
  const reuse = (entry) => { setSql(entry.sql); setActive(null); };
  const removeSaved = async (query) => {
    if (!confirm(`Delete the saved query “${query.name}”?`)) return;
    if (await run(() => del(`${base}/queries/${query.id}`), "Query deleted")) { if (active?.id === query.id) setActive(null); saved.reload(); }
  };

  const rows = result?.rows || [];
  const cols = rows.length ? Object.keys(rows[0]) : [];
  const planText = result?.explain && cols.length === 1 ? rows.map((r) => r[cols[0]]).join("\n") : null;
  const shownRows = rows.slice(0, shown);
  const visible = tables.filter((t) => !filter || t.name.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="sql-page">
      <aside className="sql-side">
        <Segmented value={side} onChange={setSide} options={[["schema", "Schema"], ["saved", `Saved${saved.data?.data?.length ? ` · ${saved.data.data.length}` : ""}`], ["history", "History"]]} />
        {side === "schema" && (
          <>
            <input className="sql-filter" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Find a table" aria-label="Find a table" />
            <div className="sql-tree">
              {visible.length === 0 && <p className="hint">{tables.length ? "No table matches." : "This database has no tables yet."}</p>}
              {visible.map((t) => (
                <div key={t.name}>
                  <div className="sql-table">
                    <button type="button" className="sql-twisty" onClick={() => { setOpen((o) => ({ ...o, [t.name]: !o[t.name] })); columnsOf(t.name); }} aria-label={open[t.name] ? "Collapse" : "Expand"}>
                      <Icon name={open[t.name] ? "chevronDown" : "chevronRight"} size={13} />
                    </button>
                    <button type="button" className="sql-name" title={`Insert SELECT * FROM ${t.name}`} onClick={() => { setSql(`SELECT *\nFROM ${quoteIdent(dialect, t.name)}\nLIMIT 100`); setActive(null); columnsOf(t.name); }}>{t.name}</button>
                    {t.resource && <Badge tone="lavender">API</Badge>}
                  </div>
                  {open[t.name] && (
                    <ul className="sql-columns">
                      {!columns[t.name] && <li className="faint">Loading…</li>}
                      {(columns[t.name] || []).map((c) => (
                        <li key={c.name}><button type="button" onClick={() => insert(quoteIdent(dialect, c.name))}><span>{c.name}</span><small>{c.type}</small></button></li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
        {side === "saved" && (
          <div className="sql-tree">
            {(saved.data?.data || []).length === 0 && <p className="hint">Queries you save are shared with everyone who manages this environment.</p>}
            {(saved.data?.data || []).map((q) => (
              <div key={q.id} className={`sql-item ${active?.id === q.id ? "on" : ""}`}>
                <button type="button" className="sql-item-main" onClick={() => load(q)}>
                  <b>{q.name}</b>
                  <small>{q.description || q.sql.replace(/\s+/g, " ").slice(0, 60)}</small>
                </button>
                <button type="button" className="sql-icon" aria-label={`Delete ${q.name}`} onClick={() => removeSaved(q)}><Icon name="trash" size={13} /></button>
              </div>
            ))}
          </div>
        )}
        {side === "history" && (
          <div className="sql-tree">
            {history.length === 0 && <p className="hint">Queries you run appear here. They stay in this browser.</p>}
            {history.map((h) => (
              <div key={h.at + h.sql} className="sql-item">
                <button type="button" className="sql-item-main" onClick={() => reuse(h)}>
                  <b className="mono">{h.sql.replace(/\s+/g, " ").slice(0, 70)}</b>
                  <small>{when(h.at)} · {h.ms} ms{h.rows !== null ? ` · ${h.rows} row${h.rows === 1 ? "" : "s"}` : h.affected !== null ? ` · ${h.affected} affected` : ""}</small>
                </button>
              </div>
            ))}
            {history.length > 0 && <Button size="sm" variant="ghost" onClick={() => { setHistory([]); writeHistory(env, []); }}>Clear history</Button>}
          </div>
        )}
      </aside>

      <div className="sql-main">
        <Card
          title={active ? active.name : "Query"}
          actions={<>
            <label className="check"><input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} /> Allow writes</label>
            <Button size="sm" onClick={() => setBuilder(!builder)}>{builder ? "Hide builder" : "Query builder"}</Button>
            <Button size="sm" onClick={() => setSaving(true)}><Icon name="check" size={14} />{active ? "Update" : "Save"}</Button>
            <Button size="sm" disabled={busy} onClick={() => execute(undefined, true)} title="Show how the database would run this query"><Icon name="search" size={14} />Explain</Button>
            <Button size="sm" variant="primary" disabled={busy} onClick={() => execute()}><Icon name="play" size={14} />Run</Button>
          </>}
        >
          {builder && (
            <>
              <SqlBuilder base={base} tables={tables} dialect={dialect} initialTable={null} onSql={(text) => { setSql(text); setActive(null); }} onWrites={(writes) => setAllowWrite(writes)} />
              <p className="hint">Each click rewrites the SQL below. Edit it by hand any time; the next click in the builder replaces your edits.</p>
            </>
          )}
          <div className="sql-editor">
            <textarea
              ref={area}
              rows={10}
              className="mono"
              value={sql}
              onChange={(e) => { setSql(e.target.value); setCaret(e.target.selectionStart); setDismissed(false); }}
              onKeyDown={onKeyDown}
              onKeyUp={(e) => { if (!["Tab", "ArrowDown", "ArrowUp"].includes(e.key) || !suggestions.length) setCaret(e.target.selectionStart); }}
              onClick={(e) => setCaret(e.target.selectionStart)}
              onBlur={() => setDismissed(true)}
              onFocus={() => setDismissed(false)}
              aria-label="SQL"
              spellCheck={false}
            />
            {suggestions.length > 0 && (
              <div className="sql-suggest" role="listbox">
                {suggestions.map((s, i) => (
                  <button key={s.value} type="button" role="option" aria-selected={i === pick} className={i === pick ? "on" : ""} onMouseDown={(e) => { e.preventDefault(); insert(s.value, true); }}>
                    <span className={`sql-kind ${s.kind}`}>{s.kind === "table" ? "T" : s.kind === "column" ? "C" : "K"}</span>{s.value}{s.note && <small>{s.note}</small>}
                  </button>
                ))}
                <span className="sql-suggest-hint">Tab to insert</span>
              </div>
            )}
          </div>
          <p className="hint">⌘ or Ctrl + Enter runs the query (just the selection, if you select part of it). ⌘S saves.{dialect ? ` Dialect: ${dialect}.` : ""}{" "}
            {dialect !== "sqlite" && <label className="check"><input type="checkbox" checked={analyze} onChange={(e) => setAnalyze(e.target.checked)} /> Explain with timings (runs the query)</label>}</p>
        </Card>

        {error && <div className="alert error"><b>The database refused the query.</b> {error}</div>}
        {result?.warnings?.map((warning, index) => (
          <div key={index} className={`alert ${warning.level === "error" ? "error" : warning.level === "info" ? "info" : "warn"} sql-warning`}>
            <div className="row"><b className="grow">{warning.title}</b><Badge tone={warning.level === "error" ? "red" : warning.level === "info" ? "" : "yellow"}>{warning.level === "error" ? "Needs fixing" : warning.level === "info" ? "Good to know" : "Action needed"}</Badge></div>
            <p>{warning.message}</p>
            <ol>{warning.steps.map((step) => <li key={step}>{step}</li>)}</ol>
            {warning.action && (
              <Button size="sm" onClick={() => {
                const table = warning.action.table || (warning.action.type === "import" ? null : warning.action.resource);
                router.visit(table ? `/envs/${env}/tables?table=${encodeURIComponent(table)}&tab=columns` : `/envs/${env}/tables`);
              }}>{warning.action.label}</Button>
            )}
          </div>
        ))}
        {result && (
          <Card
            icon={result.explain ? "search" : "eye"}
            flush
            title={result.explain ? "Query plan" : "Result"}
            actions={
              <div className="row">
                <span className="muted small">{result.affected !== undefined ? `${result.affected} row${result.affected === 1 ? "" : "s"} affected` : `${rows.length}${result.truncated ? "+" : ""} row${rows.length === 1 ? "" : "s"}`} · {result.ms} ms</span>
                {rows.length > 0 && !planText && (
                  <>
                    <Button size="sm" variant="ghost" onClick={() => { navigator.clipboard.writeText(csvOf(cols, rows)); toast("Copied as CSV"); }}>Copy CSV</Button>
                    <Button size="sm" variant="ghost" onClick={() => { navigator.clipboard.writeText(JSON.stringify(rows, null, 2)); toast("Copied as JSON"); }}>Copy JSON</Button>
                    <Button size="sm" variant="ghost" onClick={() => download("query-result.csv", csvOf(cols, rows), "text/csv")}><Icon name="arrowDown" size={13} />CSV</Button>
                  </>
                )}
              </div>
            }
          >
            {result.affected !== undefined ? (
              <div className="alert ok" style={{ margin: 16 }}>Done. {result.affected} row{result.affected === 1 ? "" : "s"} affected.</div>
            ) : planText ? (
              <pre className="code-block sql-plan">{planText}</pre>
            ) : (
              <>
                <Table rows={shownRows} columns={cols.map((c) => ({ label: c, key: c, render: (r) => formatCell(r[c]) }))} empty="No rows." />
                {rows.length > shown && <div className="sql-more"><Button size="sm" onClick={() => setShown(shown + SHOWN)}>Show {Math.min(SHOWN, rows.length - shown)} more</Button><span className="muted small">{shown} of {rows.length} shown</span></div>}
                {result.truncated && <div className="hint" style={{ padding: "0 20px 14px" }}>Showing the first 5000 rows.</div>}
              </>
            )}
          </Card>
        )}
      </div>

      {saving && (
        <SaveModal
          base={base}
          sql={sql}
          active={active}
          onClose={() => setSaving(false)}
          onSaved={(query) => { setSaving(false); setActive(query); saved.reload(); setSide("saved"); }}
        />
      )}
    </div>
  );
}

function SaveModal({ base, sql, active, onClose, onSaved }) {
  const [name, setName] = useState(active?.name || "");
  const [description, setDescription] = useState(active?.description || "");
  const [asNew, setAsNew] = useState(false);
  const [run, busy] = useAction();
  const updating = active && !asNew;
  const save = async () => {
    const body = { name: name.trim(), description: description.trim(), sql };
    const out = await run(() => (updating ? put(`${base}/queries/${active.id}`, body) : post(`${base}/queries`, body)), updating ? "Query updated" : "Query saved");
    if (out && out !== true) onSaved(out);
  };
  return (
    <Modal title={updating ? "Update saved query" : "Save query"} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !name.trim()} onClick={save}>{updating ? "Update" : "Save"}</Button></>}>
      <div className="stack">
        <Field label="Name"><input autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && save()} placeholder="Orders per day" /></Field>
        <Field label="Description" optional><input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What it answers" /></Field>
        {active && <label className="check"><input type="checkbox" checked={asNew} onChange={(e) => setAsNew(e.target.checked)} /> Save as a new query instead of changing “{active.name}”</label>}
        <pre className="code-block sql-plan">{sql}</pre>
      </div>
    </Modal>
  );
}
