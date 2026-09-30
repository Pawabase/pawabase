import { useEffect, useMemo, useState } from "react";
import { get } from "../lib/api";
import { Icon } from "./icons";
import { Segmented } from "./ui";

// Compose a SQL statement by clicking: pick a table, toggle its columns, add
// joins along foreign keys, filters, ordering and a limit. The statement is
// rebuilt on every click and handed to `onSql`; the editor underneath shows it
// and can still be edited by hand.

const MODES = [["select", "Select"], ["insert", "Insert"], ["update", "Update"], ["delete", "Delete"]];
const OPERATORS = ["=", "!=", ">", ">=", "<", "<=", "LIKE", "NOT LIKE", "IN", "IS NULL", "IS NOT NULL"];
const NO_VALUE = ["IS NULL", "IS NOT NULL"];

export function quoteIdent(dialect, name) {
  return dialect === "mysql" ? `\`${name}\`` : `"${name}"`;
}

function literal(value, column) {
  const text = String(value ?? "").trim();
  if (text.toLowerCase() === "null") return "NULL";
  const numeric = /int|real|float|double|numeric|decimal|serial/i.test(column?.type || "");
  if (/^-?\d+(\.\d+)?$/.test(text) && (numeric || !column)) return text;
  if (/^(true|false)$/i.test(text) && /bool/i.test(column?.type || "")) return text.toUpperCase();
  return `'${String(value ?? "").replace(/'/g, "''")}'`;
}

/** Build the statement from the builder's state. Exported for tests. */
export function compose(state, infos, dialect) {
  const q = (n) => quoteIdent(dialect, n);
  const { mode, table, joins, columns, where, orderBy, direction, limit, values, count } = state;
  if (!table) return "";
  const multi = joins.length > 0;
  const col = (ref) => {
    const [t, c] = ref.split(".");
    return multi ? `${q(t)}.${q(c)}` : q(c);
  };
  const columnInfo = (ref) => {
    const [t, c] = ref.split(".");
    return infos[t]?.columns?.find((x) => x.name === c);
  };
  const conditions = where.filter((w) => w.column && (NO_VALUE.includes(w.op) || String(w.value).length));
  const whereSql = conditions.length
    ? "\nWHERE " + conditions.map((w, i) => {
        const left = col(w.column);
        let right = "";
        if (w.op === "IN") right = `(${String(w.value).split(",").map((v) => literal(v.trim(), columnInfo(w.column))).join(", ")})`;
        else if (!NO_VALUE.includes(w.op)) right = " " + literal(w.value, columnInfo(w.column));
        const clause = w.op === "IN" ? `${left} IN ${right}` : `${left} ${w.op}${right}`;
        return (i ? `${w.join} ` : "") + clause;
      }).join("\n  ")
    : "";

  if (mode === "insert") {
    const names = Object.keys(values).filter((k) => String(values[k]).length);
    if (!names.length) return `INSERT INTO ${q(table)} DEFAULT VALUES`;
    const info = infos[table];
    return `INSERT INTO ${q(table)} (${names.map(q).join(", ")})\nVALUES (${names.map((n) => literal(values[n], info?.columns?.find((c) => c.name === n))).join(", ")})`;
  }
  if (mode === "update") {
    const info = infos[table];
    const sets = Object.keys(values).filter((k) => String(values[k]).length);
    if (!sets.length) return `UPDATE ${q(table)}\nSET `;
    return `UPDATE ${q(table)}\nSET ${sets.map((n) => `${q(n)} = ${literal(values[n], info?.columns?.find((c) => c.name === n))}`).join(", ")}${whereSql}`;
  }
  if (mode === "delete") return `DELETE FROM ${q(table)}${whereSql}`;

  const select = count ? "COUNT(*)" : columns.length ? columns.map((c) => (multi ? `${col(c)} AS ${q(c.replace(".", "_"))}` : col(c))).join(", ") : "*";
  let sql = `SELECT ${select}\nFROM ${q(table)}`;
  for (const j of joins) {
    sql += `\n${j.type} JOIN ${q(j.table)} ON ${q(j.left.split(".")[0])}.${q(j.left.split(".")[1])} = ${q(j.table)}.${q(j.right)}`;
  }
  sql += whereSql;
  if (orderBy && !count) sql += `\nORDER BY ${col(orderBy)} ${direction}`;
  if (limit && !count) sql += `\nLIMIT ${Number(limit) || 100}`;
  return sql;
}

const emptyState = (table = "") => ({ mode: "select", table, joins: [], columns: [], where: [], orderBy: "", direction: "ASC", limit: 100, values: {}, count: false });

export default function SqlBuilder({ base, tables, dialect, initialTable, onSql, onWrites }) {
  const [state, setState] = useState(() => emptyState(initialTable || ""));
  const [infos, setInfos] = useState({});
  const set = (patch) => setState((s) => ({ ...s, ...patch }));

  const need = [state.table, ...state.joins.map((j) => j.table)].filter(Boolean);
  useEffect(() => {
    for (const name of need) {
      if (infos[name]) continue;
      get(`${base}/tables/${name}`).then((info) => setInfos((c) => ({ ...c, [name]: info }))).catch(() => setInfos((c) => ({ ...c, [name]: { columns: [], foreign_keys: [] } })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [need.join("|")]);

  const sql = useMemo(() => compose(state, infos, dialect), [state, infos, dialect]);
  useEffect(() => { if (sql) onSql(sql); }, [sql]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { onWrites(state.mode !== "select"); }, [state.mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const info = infos[state.table];
  const multi = state.joins.length > 0;
  // Every column offered in SELECT / WHERE / ORDER BY: the base table's, plus each joined table's.
  const available = [state.table, ...state.joins.map((j) => j.table)].filter(Boolean).flatMap((t) => (infos[t]?.columns || []).map((c) => ({ ref: `${t}.${c.name}`, table: t, name: c.name, type: c.type })));
  // Joins the clicks can offer: foreign keys out of any table in the query, and tables that point back.
  const suggestions = [];
  for (const t of [state.table, ...state.joins.map((j) => j.table)].filter(Boolean)) {
    for (const fk of infos[t]?.foreign_keys || []) {
      if (!state.joins.some((j) => j.table === fk.references_table) && fk.references_table !== state.table) {
        suggestions.push({ label: `${fk.references_table}`, hint: `${t}.${fk.column} → ${fk.references_table}.${fk.references_column}`, join: { table: fk.references_table, left: `${t}.${fk.column}`, right: fk.references_column, type: "LEFT" } });
      }
    }
  }
  for (const other of tables) {
    const fk = (infos[other.name]?.foreign_keys || []).find((f) => f.references_table === state.table);
    if (fk && !state.joins.some((j) => j.table === other.name)) suggestions.push({ label: other.name, hint: `${other.name}.${fk.column} → ${state.table}.${fk.references_column}`, join: { table: other.name, left: `${state.table}.${fk.references_column}`, right: fk.column, type: "LEFT" } });
  }

  const pickTable = (table) => setState({ ...emptyState(table), mode: state.mode });
  const toggleColumn = (ref) => set({ count: false, columns: state.columns.includes(ref) ? state.columns.filter((c) => c !== ref) : [...state.columns, ref] });
  const updateWhere = (i, patch) => set({ where: state.where.map((w, j) => (j === i ? { ...w, ...patch } : w)) });
  const firstColumn = available[0]?.ref || "";

  return (
    <div className="qb">
      <div className="qb-row">
        <span className="qb-label">Action</span>
        <Segmented options={MODES} value={state.mode} onChange={(mode) => set({ mode, values: {}, columns: [], count: false })} />
      </div>

      <div className="qb-row top">
        <span className="qb-label">{state.mode === "select" ? "From" : "Table"}</span>
        <div className="qb-chips">
          {tables.map((t) => (
            <button type="button" key={t.name} className={`qb-chip ${state.table === t.name ? "on" : ""}`} onClick={() => pickTable(t.name)}>{t.name}</button>
          ))}
          {!tables.length && <span className="hint">No tables yet.</span>}
        </div>
      </div>

      {state.table && state.mode === "select" && (
        <>
          <div className="qb-row top">
            <span className="qb-label">Columns</span>
            <div className="qb-chips">
              <button type="button" className={`qb-chip ${!state.columns.length && !state.count ? "on" : ""}`} onClick={() => set({ columns: [], count: false })}>All</button>
              <button type="button" className={`qb-chip ${state.count ? "on" : ""}`} onClick={() => set({ count: !state.count })}>Count rows</button>
              {available.map((c) => (
                <button type="button" key={c.ref} className={`qb-chip ${state.columns.includes(c.ref) ? "on" : ""}`} title={c.type} onClick={() => toggleColumn(c.ref)}>
                  {multi && <span className="faint">{c.table}.</span>}{c.name}
                </button>
              ))}
            </div>
          </div>

          <div className="qb-row top">
            <span className="qb-label">Join</span>
            <div className="qb-chips">
              {state.joins.map((j) => (
                <span key={j.table} className="qb-chip on">
                  <select value={j.type} onChange={(e) => set({ joins: state.joins.map((x) => (x.table === j.table ? { ...x, type: e.target.value } : x)) })} aria-label="Join type">
                    {["LEFT", "INNER"].map((t) => <option key={t}>{t}</option>)}
                  </select>
                  {j.table}
                  <button type="button" aria-label={`Remove join ${j.table}`} onClick={() => set({ joins: state.joins.filter((x) => x.table !== j.table), columns: state.columns.filter((c) => !c.startsWith(j.table + ".")), where: state.where.filter((w) => !w.column.startsWith(j.table + ".")), orderBy: state.orderBy.startsWith(j.table + ".") ? "" : state.orderBy })}><Icon name="x" size={12} /></button>
                </span>
              ))}
              {suggestions.map((s) => (
                <button type="button" key={s.join.table} className="qb-chip ghost" title={s.hint} onClick={() => set({ joins: [...state.joins, s.join] })}><Icon name="plus" size={12} />{s.label}</button>
              ))}
              {!state.joins.length && !suggestions.length && <span className="hint">No foreign keys to join along.</span>}
            </div>
          </div>
        </>
      )}

      {state.table && (state.mode === "insert" || state.mode === "update") && (
        <div className="qb-row top">
          <span className="qb-label">{state.mode === "insert" ? "Values" : "Set"}</span>
          <div className="qb-fields">
            {(info?.columns || []).map((c) => (
              <label key={c.name} className="qb-field">
                <span>{c.name} <span className="faint">{c.type}</span></span>
                <input value={state.values[c.name] ?? ""} placeholder={c.default ? `default ${c.default}` : c.nullable ? "NULL" : ""} onChange={(e) => set({ values: { ...state.values, [c.name]: e.target.value } })} />
              </label>
            ))}
          </div>
        </div>
      )}

      {state.table && state.mode !== "insert" && (
        <div className="qb-row top">
          <span className="qb-label">Where</span>
          <div className="qb-where">
            {state.where.map((w, i) => (
              <div key={i} className="qb-cond">
                {i > 0 ? (
                  <select value={w.join} onChange={(e) => updateWhere(i, { join: e.target.value })} aria-label="Combine with"><option>AND</option><option>OR</option></select>
                ) : <span className="qb-and faint">where</span>}
                <select value={w.column} onChange={(e) => updateWhere(i, { column: e.target.value })} aria-label="Column">
                  {available.map((c) => <option key={c.ref} value={c.ref}>{multi ? c.ref : c.name}</option>)}
                </select>
                <select value={w.op} onChange={(e) => updateWhere(i, { op: e.target.value })} aria-label="Operator">{OPERATORS.map((o) => <option key={o}>{o}</option>)}</select>
                {!NO_VALUE.includes(w.op) && <input value={w.value} placeholder={w.op === "IN" ? "a, b, c" : w.op.includes("LIKE") ? "%text%" : "value"} onChange={(e) => updateWhere(i, { value: e.target.value })} />}
                <button type="button" className="icon-btn" aria-label="Remove condition" onClick={() => set({ where: state.where.filter((_, j) => j !== i) })}><Icon name="x" size={14} /></button>
              </div>
            ))}
            <button type="button" className="qb-chip ghost" disabled={!firstColumn} onClick={() => set({ where: [...state.where, { column: firstColumn, op: "=", value: "", join: "AND" }] })}><Icon name="plus" size={12} />Add condition</button>
            {state.mode !== "select" && !state.where.some((w) => w.value !== "" || NO_VALUE.includes(w.op)) && <span className="hint" style={{ color: "var(--warn)" }}>No condition: this affects every row.</span>}
          </div>
        </div>
      )}

      {state.table && state.mode === "select" && !state.count && (
        <div className="qb-row">
          <span className="qb-label">Order</span>
          <select value={state.orderBy} onChange={(e) => set({ orderBy: e.target.value })} aria-label="Order by" style={{ width: "auto", minWidth: 150 }}>
            <option value="">None</option>
            {available.map((c) => <option key={c.ref} value={c.ref}>{multi ? c.ref : c.name}</option>)}
          </select>
          <Segmented options={[["ASC", "Ascending"], ["DESC", "Descending"]]} value={state.direction} onChange={(direction) => set({ direction })} />
          <span className="qb-label" style={{ marginLeft: 8, minWidth: 0 }}>Limit</span>
          <input type="number" min="1" value={state.limit} onChange={(e) => set({ limit: e.target.value })} style={{ width: 92 }} />
        </div>
      )}
    </div>
  );
}
