import { useState } from "react";
import Layout from "../../components/Layout";
import SqlBuilder from "../../components/SqlBuilder";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Loading, Modal, PageHead, Table, formatCell, useAction } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

export default function Database({ project, env }) {
  const base = envPath(project.ref, env, "/database");
  const overview = useApi(base);
  const [table, setTable] = useState(null);
  const [sqlOpen, setSqlOpen] = useState(false);
  return (
    <Layout title="Database">
      <PageHead
        title="Database"
        description="Browse the environment's database. Bring your own with a database URL in Settings."
        actions={<Button variant="primary" onClick={() => setSqlOpen(true)}><Icon name="code" />SQL</Button>}
      />
      <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", gap: 16, alignItems: "start" }}>
        <Card flush title="Tables">
          <Loading state={overview} empty="No tables yet.">
            {(data) => (
              <div className="nav" style={{ padding: 6 }}>
                <div className="hint" style={{ padding: "4px 10px 8px" }}>{data.dialect} · {data.configured ? "your database" : "platform default"}</div>
                {data.tables.map((t) => (
                  <a key={t.name} href="#" className={table === t.name ? "active" : ""} onClick={(e) => { e.preventDefault(); setTable(t.name); }}>
                    <span className="grow">{t.name}</span>{t.resource && <Badge tone="green">{t.resource}</Badge>}
                  </a>
                ))}
              </div>
            )}
          </Loading>
        </Card>
        <div className="stack lg" style={{ minWidth: 0 }}>
          {table ? <TableView base={base} table={table} /> : <div className="calm" style={{ padding: 24 }}>Pick a table on the left, or click <b>SQL</b> to run a query.</div>}
        </div>
      </div>
      {sqlOpen && <SqlModal base={base} tables={overview.data?.tables || []} dialect={overview.data?.dialect} table={table} onClose={() => setSqlOpen(false)} />}
    </Layout>
  );
}

function TableView({ base, table }) {
  const [offset, setOffset] = useState(0);
  const info = useApi(`${base}/tables/${table}`);
  const rows = useApi(`${base}/tables/${table}/rows`, { params: { limit: 50, offset } });
  return (
    <Card flush title={<span>{table} {info.data && <span className="faint" style={{ fontWeight: 400 }}>· {info.data.rows} rows</span>}</span>} actions={<>
      <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Prev</Button>
      <Button size="sm" disabled={(rows.data?.data?.length || 0) < 50} onClick={() => setOffset(offset + 50)}>Next</Button>
    </>}>
      {info.data?.columns && (
        <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--line)" }} className="row wrap">
          {info.data.columns.map((c) => <span key={c.name} className="badge" title={JSON.stringify(c)}>{c.name}: {c.type}{c.primary_key ? " 🔑" : ""}</span>)}
        </div>
      )}
      <Loading state={rows} empty="The table is empty.">
        {(data) => {
          const cols = Object.keys(data.data[0] || {});
          return <Table rows={data.data} columns={cols.map((c) => ({ label: c, key: c, render: (r) => formatCell(r[c]) }))} />;
        }}
      </Loading>
    </Card>
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
