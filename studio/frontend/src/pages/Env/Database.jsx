import { useState } from "react";
import Layout from "../../components/Layout";
import SqlBuilder from "../../components/SqlBuilder";
import { Icon } from "../../components/icons";
import { Button, Loading, Modal, PageHead, Table, formatCell, truncate, useAction } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";

export default function Database({ env }) {
  const base = envPath(env, "/database");
  const overview = useApi(base);
  const [table, setTable] = useState(null);
  const [sqlOpen, setSqlOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [run, busy] = useAction();
  const [result, setResult] = useState(null);
  // Every resource gets its table (or the columns it gained). The same call the resource editor's "Create / migrate table" makes, for all of them at once.
  const syncTables = async () => {
    const done = await run(async () => {
      const resources = (await get(envPath(env, "/resources"), { params: { limit: 500 } })).data || [];
      const outcome = { created: [], extended: [], unchanged: 0, failed: [] };
      for (const resource of resources) {
        try {
          const answer = await post(envPath(env, `/resources/${resource.name}/migrate`));
          const statements = answer.statements || [];
          if (statements.some((sql) => /^\s*CREATE TABLE/i.test(sql))) outcome.created.push(resource.name);
          else if (statements.some((sql) => /ADD COLUMN/i.test(sql))) outcome.extended.push(resource.name);
          else outcome.unchanged += 1;
        } catch (error) {
          outcome.failed.push(`${resource.name}: ${error.message}`);
        }
      }
      return { resources: resources.length, ...outcome };
    });
    if (done && done !== true) { setResult(done); overview.reload(); }
  };
  return (
    <Layout title="Database">
      <PageHead
        title="Database"
        description="Browse the environment's database. Bring your own with a database URL in Settings."
        actions={<>
          <Button disabled={busy} onClick={syncTables}><Icon name="database" />{busy ? "Working…" : "Create tables"}</Button>
          <Button variant="primary" onClick={() => setSqlOpen(true)}><Icon name="code" />SQL</Button>
        </>}
      />
      {result && (
        <div className={`alert ${result.failed.length ? "warn" : "ok"}`} style={{ marginBottom: 16 }}>
          <b>{result.resources} resources checked.</b>{" "}
          {result.created.length} table{result.created.length === 1 ? "" : "s"} created, {result.extended.length} extended, {result.unchanged} already up to date
          {result.failed.length > 0 && <> · {result.failed.length} failed: {result.failed.slice(0, 3).join("; ")}{result.failed.length > 3 ? "…" : ""}</>}.
          <span className="hint" style={{ display: "block" }}>Columns are only ever added: nothing is dropped or retyped.</span>
        </div>
      )}
      <div className="db-layout">
        <aside className="card flush db-tables">
          <Loading state={overview} empty="No tables yet.">
            {(data) => {
              const shown = data.tables.filter((t) => t.name.toLowerCase().includes(filter.trim().toLowerCase()));
              return (
                <>
                  <div className="db-tables-head">
                    <div className="spread"><h2>Tables</h2><span className="faint" style={{ fontSize: 12 }}>{data.tables.length}</span></div>
                    <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter tables…" />
                  </div>
                  <div className="db-list">
                    {shown.map((t) => (
                      <button key={t.name} type="button" title={t.name} className={`db-link ${table === t.name ? "active" : ""}`} onClick={() => setTable(t.name)}>
                        <span className="db-name">{t.name}</span>
                        {t.resource && <span className="db-tag" title={`Resource: ${t.resource}`}>resource</span>}
                      </button>
                    ))}
                    {!shown.length && <div className="db-meta">No table matches “{filter}”.</div>}
                  </div>
                  <div className="db-meta" style={{ borderTop: "1px solid var(--line)" }}>{data.dialect} · {data.configured ? "your database" : "platform default"}</div>
                </>
              );
            }}
          </Loading>
        </aside>
        <div className="db-main">
          {table ? (
            <TableView key={table} base={base} table={table} />
          ) : (
            <div className="card db-empty"><b>Pick a table</b><span>Choose one on the left to browse its rows, or click <b style={{ fontSize: "inherit" }}>SQL</b> to run a query.</span></div>
          )}
        </div>
      </div>
      {sqlOpen && <SqlModal base={base} tables={overview.data?.tables || []} dialect={overview.data?.dialect} table={table} onClose={() => setSqlOpen(false)} />}
    </Layout>
  );
}

const PAGE = 50;

function cell(value) {
  if (value === null || value === undefined || value === "") return <span className="faint">—</span>;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") { const text = JSON.stringify(value); return <code title={text}>{truncate(text, 80)}</code>; }
  const text = String(value);
  return <span title={text.length > 40 ? text : undefined}>{text}</span>;
}

function TableView({ base, table }) {
  const [offset, setOffset] = useState(0);
  const info = useApi(`${base}/tables/${table}`);
  const rows = useApi(`${base}/tables/${table}/rows`, { params: { limit: PAGE, offset } });
  const total = info.data?.rows;
  const count = rows.data?.data?.length || 0;
  return (
    <section className="card flush">
      <div className="db-head">
        <div>
          <h2>{table}</h2>
          <div className="sub">{total !== undefined ? `${total.toLocaleString()} row${total === 1 ? "" : "s"}` : "…"}{info.data?.columns ? ` · ${info.data.columns.length} columns` : ""}</div>
        </div>
        <div className="db-pager">
          <span>{count ? `${offset + 1}–${offset + count}${total !== undefined ? ` of ${total.toLocaleString()}` : ""}` : "no rows"}</span>
          <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Prev</Button>
          <Button size="sm" disabled={count < PAGE || (total !== undefined && offset + count >= total)} onClick={() => setOffset(offset + PAGE)}>Next</Button>
        </div>
      </div>
      {info.data?.columns && (
        <div className="db-cols">
          {info.data.columns.map((c) => <span key={c.name} className="badge" title={JSON.stringify(c)}><b>{c.name}</b>{c.type}{c.primary_key ? " · key" : ""}</span>)}
        </div>
      )}
      <Loading state={rows} empty="The table is empty.">
        {(data) => {
          const cols = Object.keys(data.data[0] || {});
          return (
            <div className="db-grid">
              <table>
                <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>{data.data.map((r, i) => <tr key={r.id ?? i}>{cols.map((c) => <td key={c} className={typeof r[c] === "number" ? "num" : undefined}>{cell(r[c])}</td>)}</tr>)}</tbody>
              </table>
            </div>
          );
        }}
      </Loading>
    </section>
  );
}

function SqlModal({ base, tables, dialect, table, onClose }) {
  const [sql, setSql] = useState(table ? `SELECT *\nFROM "${table}"\nLIMIT 100` : "SELECT 1 AS ok");
  const [builder, setBuilder] = useState(true);
  const [allowWrite, setAllowWrite] = useState(false);
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  const execute = async () => {
    const r = await run(() => post(`${base}/query`, { sql, allow_write: allowWrite }));
    if (r) setResult(r);
  };
  return (
    <Modal
      title="SQL"
      wide
      onClose={onClose}
      footer={<>
        <label className="check" style={{ fontSize: 12.5, marginRight: "auto" }}><input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} /> allow writes</label>
        <Button onClick={onClose}>Close</Button>
        <Button variant="primary" disabled={busy} onClick={execute}>Run</Button>
      </>}
    >
      <div className="spread" style={{ marginBottom: 8 }}>
        <b>Query builder</b>
        <Button size="sm" onClick={() => setBuilder(!builder)}>{builder ? "Hide" : "Show"}</Button>
      </div>
      {builder && (
        <>
          <SqlBuilder base={base} tables={tables} dialect={dialect} initialTable={table} onSql={setSql} onWrites={(writes) => setAllowWrite(writes)} />
          <p className="hint" style={{ margin: "10px 0 14px" }}>Each click rewrites the SQL below. Edit it by hand any time; the next click in the builder replaces your edits.</p>
        </>
      )}
      <b style={{ display: "block", marginBottom: 8 }}>SQL</b>
      <textarea
        rows={6}
        className="mono"
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") execute(); }}
        autoFocus
      />
      {result && (
        <div style={{ marginTop: 12 }}>
          {result.affected !== undefined ? (
            <div className="alert ok">{result.affected} row(s) affected.</div>
          ) : (
            <div className="card flush"><Table rows={result.rows} columns={Object.keys(result.rows[0] || {}).map((c) => ({ label: c, key: c, render: (r) => formatCell(r[c]) }))} empty="No rows." /></div>
          )}
          {result.truncated && <div className="hint">Showing the first 5000 rows.</div>}
        </div>
      )}
    </Modal>
  );
}
