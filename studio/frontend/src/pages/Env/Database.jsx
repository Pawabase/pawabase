import { useState } from "react";
import Layout, { dataSubnav } from "../../components/Layout";
import SqlBuilder from "../../components/SqlBuilder";
import { Icon } from "../../components/icons";
import { Button, Card, Loading, PageHead, Table, formatCell, truncate, useAction } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";

/** The SQL page of Data: run queries against the environment's database, with a builder for people who would rather click. */
export default function Database({ env }) {
  const base = envPath(env, "/database");
  const overview = useApi(base);
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
    <Layout title="SQL" subnav={dataSubnav(env, "database")}>
      <PageHead
        title="SQL"
        description="Run queries against the environment's database. Browse a resource's records from Resources. Bring your own database with a database URL in Settings."
        actions={<Button disabled={busy} onClick={syncTables}><Icon name="database" />{busy ? "Working…" : "Create tables"}</Button>}
      />
      {result && (
        <div className={`alert ${result.failed.length ? "warn" : "ok"}`} style={{ marginBottom: 16 }}>
          <b>{result.resources} resources checked.</b>{" "}
          {result.created.length} table{result.created.length === 1 ? "" : "s"} created, {result.extended.length} extended, {result.unchanged} already up to date
          {result.failed.length > 0 && <> · {result.failed.length} failed: {result.failed.slice(0, 3).join("; ")}{result.failed.length > 3 ? "…" : ""}</>}.
          <span className="hint" style={{ display: "block" }}>Columns are only ever added: nothing is dropped or retyped.</span>
        </div>
      )}
      <Loading state={overview}>
        {(data) => <SqlConsole base={base} tables={data.tables || []} dialect={data.dialect} />}
      </Loading>
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

export function TableView({ base, table }) {
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

function SqlConsole({ base, tables, dialect }) {
  const [sql, setSql] = useState("SELECT 1 AS ok");
  const [builder, setBuilder] = useState(true);
  const [allowWrite, setAllowWrite] = useState(false);
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  const execute = async () => {
    const r = await run(() => post(`${base}/query`, { sql, allow_write: allowWrite }));
    if (r) setResult(r);
  };
  return (
    <div className="stack lg">
      <Card
        title="Query"
        actions={<>
          <label className="check" style={{ fontSize: 12.5 }}><input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} /> allow writes</label>
          <Button size="sm" onClick={() => setBuilder(!builder)}>{builder ? "Hide builder" : "Query builder"}</Button>
          <Button size="sm" variant="primary" disabled={busy} onClick={execute}><Icon name="play" size={14} /> Run</Button>
        </>}
      >
        {builder && (
          <>
            <SqlBuilder base={base} tables={tables} dialect={dialect} initialTable={null} onSql={setSql} onWrites={(writes) => setAllowWrite(writes)} />
            <p className="hint" style={{ margin: "10px 0 14px" }}>Each click rewrites the SQL below. Edit it by hand any time; the next click in the builder replaces your edits.</p>
          </>
        )}
        <textarea
          rows={8}
          className="mono"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") execute(); }}
          aria-label="SQL"
          spellCheck={false}
        />
        <p className="hint" style={{ margin: "8px 0 0" }}>⌘ or Ctrl + Enter runs the query. {dialect ? `Dialect: ${dialect}.` : ""}</p>
      </Card>
      {result && (
        <Card flush title="Result" actions={result.rows ? <span className="muted small">{result.rows.length} row{result.rows.length === 1 ? "" : "s"}</span> : undefined}>
          {result.affected !== undefined ? (
            <div className="alert ok" style={{ margin: 16 }}>{result.affected} row(s) affected.</div>
          ) : (
            <Table rows={result.rows} columns={Object.keys(result.rows[0] || {}).map((c) => ({ label: c, key: c, render: (r) => formatCell(r[c]) }))} empty="No rows." />
          )}
          {result.truncated && <div className="hint" style={{ padding: "0 20px 14px" }}>Showing the first 5000 rows.</div>}
        </Card>
      )}
    </div>
  );
}
