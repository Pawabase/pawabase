import { router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../../components/Layout";
import { useDataSubnav } from "../../components/DataNav";
import SqlBuilder from "../../components/SqlBuilder";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Loading, PageHead, Table, formatCell, truncate, useAction } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";

/** The SQL page of Data: run queries against the environment's database, with a builder for people who would rather click. */
export default function Database({ env }) {
  const base = envPath(env, "/database");
  const overview = useApi(base);
  const { subnav } = useDataSubnav(env, "database");
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
    <Layout title="SQL" subnav={subnav}>
      <PageHead
        title="SQL"
        description="Run queries against the environment's database. Browse a table's rows in the Table editor. Bring your own database with a database URL in Settings."
        actions={<Button disabled={busy} onClick={syncTables}><Icon name="database" />{busy ? "Working…" : "Create tables"}</Button>}
      />
      {result && (
        <div className={`alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] ${result.failed.length ? "warn" : "ok"}`} style={{ marginBottom: 16 }}>
          <b>{result.resources} resources checked.</b>{" "}
          {result.created.length} table{result.created.length === 1 ? "" : "s"} created, {result.extended.length} extended, {result.unchanged} already up to date
          {result.failed.length > 0 && <> · {result.failed.length} failed: {result.failed.slice(0, 3).join("; ")}{result.failed.length > 3 ? "…" : ""}</>}.
          <span className="hint text-xs font-book text-muted leading-[1.45]" style={{ display: "block" }}>Columns are only ever added: nothing is dropped or retyped.</span>
        </div>
      )}
      <Loading state={overview}>
        {(data) => <SqlConsole env={env} base={base} tables={data.tables || []} dialect={data.dialect} />}
      </Loading>
    </Layout>
  );
}

const PAGE = 50;

function cell(value) {
  if (value === null || value === undefined || value === "") return <span className="faint text-faint">—</span>;
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
    <section className="card bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-md p-4 [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [&.flush_.card-head_+_*]:[border-top:1px_solid_var(--line)] [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:transform-[none]! [a&:hover]:shadow-none [border:1px_solid_var(--line)] shadow-none [&_>_.spread:first-child]:mb-3! [&_>_.spread_h2]:text-base [&_>_.spread_h2]:font-semibold [&_>_.spread_h2]:tracking-[-0.01em] [&.flush_>_.card-head_+_*]:border-t-0 [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&_>_.spread_.chips]:gap-0 [&_>_.spread_.chips]:flex-nowrap [&_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_>_.spread_.chips]:rounded-sm [&_>_.spread_.chips]:overflow-hidden [&_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_>_.spread_.chip]:p-[0_10px] [&_>_.spread_.chip]:border-0 [&_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_>_.spread_.chip]:rounded-none [&_>_.spread_.chip]:bg-transparent [&_>_.spread_.chip]:text-muted [&_>_.spread_.chip]:text-xs [&_>_.spread_.chip]:font-medium-plus [&_>_.spread_.chip:first-child]:border-l-0 [&_>_.spread_.chip.active]:bg-press [&_>_.spread_.chip.active]:text-text [&_>_.spread_.chip.on]:bg-press [&_>_.spread_.chip.on]:text-text [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100 flush">
      <div className="db-head flex items-center justify-between gap-3 p-[14px_20px] flex-wrap [&_h2]:m-0 [&_h2]:text-title [&_h2]:font-mono [&_h2]:wrap-anywhere [&_.sub]:text-ui-sm [&_.sub]:text-muted [&_.sub]:mt-0.5">
        <div>
          <h2>{table}</h2>
          <div className="sub">{total !== undefined ? `${total.toLocaleString()} row${total === 1 ? "" : "s"}` : "…"}{info.data?.columns ? ` · ${info.data.columns.length} columns` : ""}</div>
        </div>
        <div className="db-pager flex items-center gap-3 text-ui-sm text-muted justify-between min-h-12 p-[0_var(--gutter)] [border-top:1px_solid_var(--line)]">
          <span>{count ? `${offset + 1}–${offset + count}${total !== undefined ? ` of ${total.toLocaleString()}` : ""}` : "no rows"}</span>
          <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Prev</Button>
          <Button size="sm" disabled={count < PAGE || (total !== undefined && offset + count >= total)} onClick={() => setOffset(offset + PAGE)}>Next</Button>
        </div>
      </div>
      {info.data?.columns && (
        <div className="db-cols flex gap-1.5 p-[10px_20px] [border-top:1px_solid_var(--line)] overflow-x-auto [scrollbar-width:thin] [&_.badge]:shrink-0 [&_.badge]:font-mono [&_.badge]:font-medium [&_.badge_b]:font-bold [&_.badge_b]:text-text [&_.badge_b]:mr-[3px]">
          {info.data.columns.map((c) => <span key={c.name} className="badge inline-flex items-center gap-1 p-[1px_7px] rounded-xs text-caption font-medium-plus bg-sunken text-muted whitespace-nowrap [&.green]:text-ok [&.green]:bg-ok-soft [&.red]:text-danger [&.red]:bg-danger-soft [&.yellow]:text-warn [&.yellow]:bg-warn-soft [&.blue]:text-info [&.blue]:bg-info-soft [&.brand]:text-brand-strong [&.brand]:bg-brand-soft [&_.dot]:w-1.5 [&_.dot]:h-1.5 [&_.dot]:rounded-full [&_.dot]:bg-[currentColor]" title={JSON.stringify(c)}><b>{c.name}</b>{c.type}{c.primary_key ? " · key" : ""}</span>)}
        </div>
      )}
      <Loading state={rows} empty="The table is empty.">
        {(data) => {
          const cols = Object.keys(data.data[0] || {});
          return (
            <div className="db-grid max-h-[68vh] overflow-auto [border-top:1px_solid_var(--line)] [&_table]:w-[max-content] [&_table]:min-w-[100%] [&_th]:sticky [&_th]:top-0 [&_th]:z-[1] [&_th]:bg-surface [&_th]:whitespace-nowrap [&_th]:shadow-none [&_th]:p-[9px_16px] [&_td]:p-[9px_16px] [&_td]:max-w-[320px] [&_td]:overflow-hidden [&_td]:text-ellipsis [&_td]:whitespace-nowrap [&_td]:text-ui [&_td_code]:text-xs [&_td.num]:text-right [&_td.num]:tabular-nums">
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

function SqlConsole({ env, base, tables, dialect }) {
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
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <Card
        title="Query"
        actions={<>
          <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui" style={{ fontSize: 12.5 }}><input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} /> allow writes</label>
          <Button size="sm" onClick={() => setBuilder(!builder)}>{builder ? "Hide builder" : "Query builder"}</Button>
          <Button size="sm" variant="primary" disabled={busy} onClick={execute}><Icon name="play" size={14} /> Run</Button>
        </>}
      >
        {builder && (
          <>
            <SqlBuilder base={base} tables={tables} dialect={dialect} initialTable={null} onSql={setSql} onWrites={(writes) => setAllowWrite(writes)} />
            <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: "10px 0 14px" }}>Each click rewrites the SQL below. Edit it by hand any time; the next click in the builder replaces your edits.</p>
          </>
        )}
        <textarea
          rows={8}
          className="mono font-mono text-ui-sm"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") execute(); }}
          aria-label="SQL"
          spellCheck={false}
        />
        <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: "8px 0 0" }}>⌘ or Ctrl + Enter runs the query. {dialect ? `Dialect: ${dialect}.` : ""}</p>
      </Card>
      {result?.warnings?.map((warning, index) => (
        <div key={index} className={`alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] ${warning.level === "error" ? "error" : warning.level === "info" ? "info" : "warn"} sql-warning flex flex-col gap-2 [&_p]:m-0 [&_ol]:m-0 [&_ol]:pl-5 [&_ol]:flex [&_ol]:flex-col [&_ol]:gap-1 [&_.btn]:self-start`}>
          <div className="spread flex items-center justify-between gap-3">
            <b>{warning.title}</b>
            <Badge tone={warning.level === "error" ? "red" : warning.level === "info" ? "" : "yellow"}>{warning.level === "error" ? "Needs fixing" : warning.level === "info" ? "Good to know" : "Action needed"}</Badge>
          </div>
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
        <Card flush title="Result" actions={result.rows ? <span className="muted text-muted small text-ui-sm">{result.rows.length} row{result.rows.length === 1 ? "" : "s"}</span> : undefined}>
          {result.affected !== undefined ? (
            <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] ok" style={{ margin: 16 }}>{result.affected} row(s) affected.</div>
          ) : (
            <Table rows={result.rows} columns={Object.keys(result.rows[0] || {}).map((c) => ({ label: c, key: c, render: (r) => formatCell(r[c]) }))} empty="No rows." />
          )}
          {result.truncated && <div className="hint text-xs font-book text-muted leading-[1.45]" style={{ padding: "0 20px 14px" }}>Showing the first 5000 rows.</div>}
        </Card>
      )}
    </div>
  );
}
