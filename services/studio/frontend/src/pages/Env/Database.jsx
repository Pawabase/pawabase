import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Loading, PageHead, Table, formatCell, useAction } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

export default function Database({ project, env }) {
  const base = envPath(project.ref, env, "/database");
  const overview = useApi(base);
  const [table, setTable] = useState(null);
  return (
    <Layout title="Database">
      <PageHead title="Database" description="Browse the environment's database and run SQL. Bring your own with a database URL in Settings." />
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
          {table && <TableView base={base} table={table} />}
          <SqlConsole base={base} />
        </div>
      </div>
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

function SqlConsole({ base }) {
  const [sql, setSql] = useState("SELECT 1 AS ok");
  const [allowWrite, setAllowWrite] = useState(false);
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  return (
    <Card title="SQL" actions={<>
      <label className="check" style={{ fontSize: 12.5 }}><input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} /> allow writes</label>
      <Button variant="primary" size="sm" disabled={busy} onClick={async () => { const r = await run(() => post(`${base}/query`, { sql, allow_write: allowWrite })); if (r) setResult(r); }}>Run</Button>
    </>}>
      <textarea rows={6} value={sql} onChange={(e) => setSql(e.target.value)} />
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
    </Card>
  );
}
