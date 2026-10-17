import { useEffect, useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { useDataSubnav } from "../../components/DataNav";
import { Icon } from "../../components/icons";
import { Badge, Button, PageTabs, Popover, Sheet, Spinner, copyToClipboard, useAction, useToast } from "../../components/ui";
import { HistoryPanel, IndexesPanel, SchemaPanel } from "../../components/SchemaPanels";
import { DefinitionSheet } from "../../components/definitions/DefinitionSheet";
import { editable } from "../../lib/kinds";
import { del, envPath, get, post, put, useApi } from "../../lib/api";

/**
 * The table editor: browse the environment's database like a spreadsheet. Tables open as tabs (kept per
 * environment in this browser), each with its own filter, sort and page; a row opens in a side sheet.
 */

const PAGE_SIZES = [50, 100, 250, 500];

function storageKey(env) {
  return `pawabase.tables.${env}`;
}

function readTabs(env) {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey(env)) || "{}");
    return { open: Array.isArray(saved.open) ? saved.open : [], active: saved.active || null };
  } catch {
    return { open: [], active: null };
  }
}

function writeTabs(env, open, active) {
  try { localStorage.setItem(storageKey(env), JSON.stringify({ open, active })); } catch { /* tabs last for this page only */ }
}

export default function Tables({ env }) {
  const base = envPath(env, "/database");
  const [tabs, setTabs] = useState(() => {
    const saved = readTabs(env);
    const wanted = new URLSearchParams(window.location.search).get("table");
    if (wanted && !saved.open.includes(wanted)) saved.open = [...saved.open, wanted];
    return { open: saved.open, active: wanted || saved.active || saved.open[0] || null };
  });
  // ?table=x&tab=schema (a link from the SQL page or Topology) opens that one table straight to its Schema tab.
  const [deepLink] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return { table: params.get("table"), tab: params.get("tab") || "data" };
  });
  const { subnav: dataSubnav, tables, loading, reload } = useDataSubnav(env, tabs.active);
  const [expanded, setExpanded] = useState(false);
  const open = (name) => setTabs((current) => ({ open: current.open.includes(name) ? current.open : [...current.open, name], active: name }));
  const close = (name) => setTabs((current) => {
    const at = current.open.indexOf(name);
    const rest = current.open.filter((n) => n !== name);
    const active = current.active === name ? rest[Math.min(at, rest.length - 1)] || null : current.active;
    return { open: rest, active };
  });
  const subnav = {
    ...dataSubnav,
    title: "Table editor",
    onSelect: open,
    groups: [
      { ...dataSubnav.groups[0], items: dataSubnav.groups[0].items.map(({ href, ...item }) => item) },
      dataSubnav.groups[1],
    ],
  };

  // Forget tabs whose table no longer exists.
  useEffect(() => {
    if (loading) return;
    const names = new Set(tables.map((t) => t.name));
    setTabs((current) => {
      const open = current.open.filter((name) => names.has(name));
      const active = names.has(current.active) ? current.active : open[0] || null;
      return open.length === current.open.length && active === current.active ? current : { open, active };
    });
  }, [loading]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    writeTabs(env, tabs.open, tabs.active);
    const url = new URL(window.location.href);
    if (tabs.active) url.searchParams.set("table", tabs.active);
    else url.searchParams.delete("table");
    window.history.replaceState({}, "", `${url.pathname}${url.search}`);
  }, [env, tabs]);

  useEffect(() => {
    if (!expanded) return undefined;
    // Escape leaves full screen, unless a row sheet or menu is open: then Escape is theirs.
    const key = (event) => { if (event.key === "Escape" && !document.querySelector(".sheet, .popover")) setExpanded(false); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [expanded]);

  return (
    <Layout title="Table editor" subnav={subnav} full>
      <div className={`tbl-editor ${expanded ? "expanded" : ""}`}>
        <div className="tbl-tabs" role="tablist" aria-label="Open tables">
          {tabs.open.map((name) => (
            <div key={name} role="tab" aria-selected={tabs.active === name} className={`tbl-tab ${tabs.active === name ? "active" : ""}`}>
              <button type="button" className="tbl-tab-name" onClick={() => open(name)} title={name}><Icon name="resources" size={13} /><span>{name}</span></button>
              <button type="button" className="tbl-tab-close" onClick={() => close(name)} aria-label={`Close ${name}`}><Icon name="x" size={12} /></button>
            </div>
          ))}
          <TablePicker tables={tables} onPick={open} />
          <span className="grow" />
          <button type="button" className="tbl-icon" onClick={() => setExpanded(!expanded)} title={expanded ? "Exit full screen (Esc)" : "View full screen"} aria-label={expanded ? "Exit full screen" : "View full screen"}>
            <Icon name={expanded ? "minimize" : "maximize"} size={15} />
          </button>
        </div>
        {tabs.active ? (
          <TableTab key={tabs.active} env={env} base={base} table={tabs.active} resource={tables.find((t) => t.name === tabs.active)?.resource} initialView={tabs.active === deepLink.table ? deepLink.tab : "data"} onTablesChanged={reload} onClose={() => { reload(); close(tabs.active); }} />
        ) : (
          <Welcome tables={tables} loading={loading} onPick={open} />
        )}
      </div>
    </Layout>
  );
}

function TablePicker({ tables, onPick }) {
  const [query, setQuery] = useState("");
  const shown = tables.filter((t) => t.name.toLowerCase().includes(query.toLowerCase().trim()));
  return (
    <Popover
      label="Open a table"
      width={300}
      trigger={(props) => <button type="button" className="tbl-icon" title="Open a table in a new tab" aria-label="Open a table in a new tab" {...props}><Icon name="plus" size={15} /></button>}
    >
      {({ close }) => (
        <>
          <div className="pop-search"><Icon name="search" size={14} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a table…" aria-label="Find a table" /></div>
          <div className="pop-list" style={{ maxHeight: 360 }}>
            {shown.map((t) => (
              <button key={t.name} type="button" role="option" className="pop-item" onClick={() => { close(false); onPick(t.name); }}>
                <Icon name="resources" size={14} className="faint" />
                <span className="grow"><b className="mono">{t.name}</b>{t.resource && <small>resource {t.resource}</small>}</span>
              </button>
            ))}
            {shown.length === 0 && <p className="pop-empty">No table matches.</p>}
          </div>
        </>
      )}
    </Popover>
  );
}

function Welcome({ tables, loading, onPick }) {
  return (
    <div className="tbl-welcome">
      <span className="tbl-welcome-icon"><Icon name="resources" size={20} /></span>
      <h2>Open a table</h2>
      <p>Pick a table on the left, or from the list below. Each one opens in its own tab.</p>
      {loading ? <Spinner /> : (
        <div className="tbl-welcome-list">
          {tables.slice(0, 12).map((t) => (
            <button key={t.name} type="button" onClick={() => onPick(t.name)}><Icon name="resources" size={13} /><span className="mono">{t.name}</span></button>
          ))}
        </div>
      )}
    </div>
  );
}

/** JSON stored as text (Postgres jsonb arrives this way), parsed so it can be shown as structure. */
function asJson(value) {
  if (typeof value !== "string" || !/^\s*[[{]/.test(value)) return undefined;
  try { return JSON.parse(value); } catch { return undefined; }
}

function display(value) {
  if (value === null || value === undefined) return { text: "NULL", kind: "null" };
  if (asJson(value) !== undefined) return { text: value, kind: "json" };
  if (typeof value === "boolean") return { text: value ? "true" : "false", kind: "bool" };
  if (typeof value === "number") return { text: String(value), kind: "num" };
  if (typeof value === "object") return { text: JSON.stringify(value), kind: "json" };
  return { text: String(value), kind: "text" };
}

const VIEWS = [
  ["data", "Data", "database"],
  ["schema", "Schema", "schemas"],
  ["history", "History", "clock"],
  ["settings", "Settings", "settings"],
];

function TableTab({ env, base, table, resource, initialView = "data", onTablesChanged, onClose }) {
  const [view, setView] = useState(initialView);
  const info = useApi(`${base}/tables/${table}`);
  const columns = info.data?.columns || [];
  return (
    <div className="tbl-body">
      <div className="tbl-identity">
        <b className="mono tbl-title">{table}</b>
        <span className="faint small">{info.data ? `${(info.data.rows ?? 0).toLocaleString()} rows · ${columns.length} columns` : "…"}</span>
        {resource && <Badge tone="brand">resource <b>{resource}</b></Badge>}
      </div>
      <PageTabs
        tabs={VIEWS.map(([value, label, icon]) => ({ value, label: <><Icon name={icon} size={14} />{label}</> }))}
        value={view}
        onChange={setView}
      />
      {view === "data" && <DataView env={env} base={base} table={table} columns={columns} info={info} />}
      {view === "schema" && (
        resource
          ? <ResourceSchema env={env} resource={resource} onRowsChanged={info.reload} onTablesChanged={onTablesChanged} />
          : <UnclaimedSchema env={env} table={table} columns={columns} onClaimed={() => onTablesChanged?.()} />
      )}
      {view === "history" && (resource
        ? <div style={{ padding: 16 }}><HistoryPanel env={env} name={resource} onChanged={info.reload} /></div>
        : <div className="tbl-state">No resource defined for this table yet — define one from the Schema tab to track changes.</div>
      )}
      {view === "settings" && <TableSettings env={env} table={table} resource={resource} onDeleted={onClose} onDisabled={onTablesChanged} />}
    </div>
  );
}

function DataView({ env, base, table, columns, info }) {
  const [page, setPage] = useState({ offset: 0, size: 100 });
  const [sort, setSort] = useState(null); // [column, "asc" | "desc"]
  const [filter, setFilter] = useState(null); // { column, op, value }
  const [row, setRow] = useState(null);
  const params = {
    limit: page.size,
    offset: page.offset,
    ...(sort ? { sort: sort[0], order: sort[1] } : {}),
    ...(filter ? { filter_column: filter.column, filter_op: filter.op, filter_value: filter.value } : {}),
  };
  const rows = useApi(`${base}/tables/${table}/rows`, { params });
  const data = rows.data?.data || [];
  const total = filter ? rows.data?.total : info.data?.rows;
  const names = columns.length ? columns.map((c) => c.name) : Object.keys(data[0] || {});
  const types = useMemo(() => Object.fromEntries(columns.map((c) => [c.name, c])), [columns]);
  const toggleSort = (name) => {
    setSort((current) => (!current || current[0] !== name ? [name, "asc"] : current[1] === "asc" ? [name, "desc"] : null));
    setPage((p) => ({ ...p, offset: 0 }));
  };
  const last = page.offset + data.length;
  return (
    <>
      <div className="tbl-toolbar">
        <span className="grow" />
        <div className="row" style={{ gap: 6 }}>
          <FilterButton columns={names} filter={filter} onApply={(next) => { setFilter(next); setPage((p) => ({ ...p, offset: 0 })); }} />
          <SortButton columns={names} sort={sort} onApply={(next) => { setSort(next); setPage((p) => ({ ...p, offset: 0 })); }} />
          <Button size="sm" onClick={() => { info.reload(); rows.reload(); }} title="Reload rows"><Icon name="refresh" size={14} /></Button>
        </div>
      </div>

      <div className="tbl-grid-wrap">
        {rows.error ? <div className="alert error" style={{ margin: 16 }}>{rows.error.message}</div> : (
          <table className="tbl-grid">
            <thead>
              <tr>
                <th className="tbl-num">#</th>
                {names.map((name) => {
                  const c = types[name];
                  const sorted = sort && sort[0] === name ? sort[1] : null;
                  return (
                    <th key={name} onClick={() => toggleSort(name)} className={sorted ? "sorted" : ""} title={`Sort by ${name}`}>
                      <span className="tbl-th">
                        {c?.primary_key && <Icon name="keys" size={11} className="tbl-pk" />}
                        <span className="tbl-th-name">{name}</span>
                        {c?.type && <span className="tbl-th-type">{String(c.type).toLowerCase()}</span>}
                        {sorted && <Icon name="chevronDown" size={12} style={{ transform: sorted === "asc" ? "rotate(180deg)" : "none" }} />}
                      </span>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {data.map((r, i) => (
                <tr key={r.id ?? `${page.offset}-${i}`} onClick={() => setRow(r)}>
                  <td className="tbl-num">{page.offset + i + 1}</td>
                  {names.map((name) => {
                    const { text, kind } = display(r[name]);
                    return <td key={name} className={`tbl-cell ${kind}`} title={text.length > 40 ? text : undefined}>{text}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rows.loading && !rows.data && <div className="tbl-state"><Spinner /> Loading rows…</div>}
        {rows.data && data.length === 0 && <div className="tbl-state">{filter ? "No rows match this filter." : "This table is empty."}</div>}
      </div>

      <div className="tbl-footer">
        <span>{data.length ? `Rows ${(page.offset + 1).toLocaleString()}–${last.toLocaleString()}${total != null ? ` of ${total.toLocaleString()}` : ""}` : "No rows"}{filter && <> · filtered</>}{rows.loading && rows.data && <> · <Spinner /></>}</span>
        <div className="row" style={{ gap: 8 }}>
          <label className="row small muted" style={{ gap: 6 }}>Rows per page
            <select value={page.size} onChange={(e) => setPage({ offset: 0, size: Number(e.target.value) })} style={{ width: 80, height: 28 }}>
              {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <div className="tbl-pager">
            <button type="button" disabled={page.offset === 0} onClick={() => setPage((p) => ({ ...p, offset: 0 }))} aria-label="First page"><Icon name="chevronRight" size={13} className="rotate-180" /><Icon name="chevronRight" size={13} className="rotate-180" style={{ marginLeft: -8 }} /></button>
            <button type="button" disabled={page.offset === 0} onClick={() => setPage((p) => ({ ...p, offset: Math.max(0, p.offset - p.size) }))} aria-label="Previous page"><Icon name="chevronRight" size={13} className="rotate-180" /></button>
            <span className="tbl-page">Page {Math.floor(page.offset / page.size) + 1}{total != null ? ` of ${Math.max(1, Math.ceil(total / page.size))}` : ""}</span>
            <button type="button" disabled={data.length < page.size || (total != null && last >= total)} onClick={() => setPage((p) => ({ ...p, offset: p.offset + p.size }))} aria-label="Next page"><Icon name="chevronRight" size={13} /></button>
          </div>
        </div>
      </div>

      {row && <RowSheet table={table} row={row} columns={names} types={types} onClose={() => setRow(null)} />}
    </>
  );
}

/** A resource-backed table's Schema tab: the definition itself (fields, access, relations, behaviour), the live
 *  table's indexes, and what's waiting to be applied — reusing the editor and panels built for this, just opened
 *  from the table instead of a separate Resources page. */
function ResourceSchema({ env, resource, onRowsChanged, onTablesChanged }) {
  const path = envPath(env, `/resources/${resource}`);
  const item = useApi(path);
  const [editing, setEditing] = useState(false);
  if (!item.data) return <div className="tbl-state"><Spinner /></div>;
  return (
    <div className="stack" style={{ padding: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="muted">Fields, access and relations for <b className="mono">{resource}</b>.</span>
        <Button size="sm" variant="primary" onClick={() => setEditing(true)}>Edit definition</Button>
      </div>
      <SchemaPanel env={env} name={resource} onChanged={() => { item.reload(); onRowsChanged?.(); }} onAdopted={() => item.reload()} />
      <IndexesPanel env={env} name={resource} onChanged={item.reload} />
      {editing && (
        <DefinitionSheet
          kind="resources"
          env={env}
          editing={{ isNew: false, key: resource, body: editable(item.data) }}
          onSave={(body) => put(path, body)}
          onDelete={() => del(path)}
          onClose={(result) => {
            setEditing(false);
            if (result === undefined) return;
            item.reload();
            onRowsChanged?.();
            if (result === null) onTablesChanged?.(); // the resource (and its tab's data) is gone
          }}
        />
      )}
    </div>
  );
}

/** A plain SQL table with no resource yet: its raw columns, and a way to define one from them. */
function UnclaimedSchema({ env, table, columns, onClaimed }) {
  const suggestion = useApi(envPath(env, "/schema/tables"));
  const [run, busy] = useAction();
  const [name, setName] = useState(table);
  const found = (suggestion.data?.data || []).find((t) => t.table === table);
  const claim = async () => {
    const definition = found.definition;
    const result = await run(async () => {
      await post(envPath(env, "/resources"), {
        name,
        table,
        primary_key: definition.primary_key,
        id_type: definition.id_type,
        timestamps: definition.timestamps,
        fields: definition.fields_,
        operations: {},
      });
      return post(envPath(env, `/resources/${name}/schema/acknowledge`), { note: `Imported from the existing table ${table}` });
    }, `${name} is now a resource`);
    if (result) onClaimed(name);
  };
  return (
    <div className="stack" style={{ padding: 16 }}>
      <p className="muted">This table has no resource definition, so it has no REST API, access control or schema history — just the raw columns below.</p>
      <div className="row wrap" style={{ gap: 6 }}>
        {columns.map((c) => <Badge key={c.name}>{c.name} · {String(c.type).toLowerCase()}</Badge>)}
      </div>
      {suggestion.loading ? <Spinner /> : found ? (
        <div className="stack sm">
          <label className="row small muted" style={{ gap: 6 }}>Resource name
            <input className="mono" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 200 }} />
          </label>
          {found.problem && <div className="alert warn">{found.problem}</div>}
          {found.skipped.length > 0 && <span className="hint">Left out (not usable as field names): {found.skipped.join(", ")}</span>}
          <div className="row"><Button variant="primary" disabled={busy || !name.trim()} onClick={claim}><Icon name="database" size={14} />Define a resource</Button></div>
        </div>
      ) : <span className="hint">Reload the Schema tab once the table has finished loading to define a resource from it.</span>}
    </div>
  );
}

/** Settings: pause the resource's REST API without touching its data, or remove the table for good. */
function TableSettings({ env, table, resource, onDeleted, onDisabled }) {
  const [run, busy] = useAction();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const disable = async () => {
    const path = envPath(env, `/resources/${resource}`);
    const result = await run(async () => {
      const current = await get(path);
      const operations = Object.fromEntries(Object.entries(current.operations || {}).map(([op, setting]) => [op, { ...setting, enabled: false }]));
      return put(path, { ...editable(current), operations });
    }, "The resource's REST API is now off. Fields, history and the table are untouched.");
    if (result) onDisabled?.();
  };
  const dropTable = async () => {
    const result = await run(async () => {
      if (resource) await del(envPath(env, `/resources/${resource}`));
      await del(envPath(env, `/database/tables/${table}`));
    }, `${table} is gone`);
    if (result) onDeleted?.();
  };
  return (
    <div className="stack" style={{ padding: 16, maxWidth: 560 }}>
      {resource && (
        <div className="card" style={{ padding: 14 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <b>Disable the API</b>
              <p className="muted">Turns off every operation on <span className="mono">{resource}</span> (list, get, create, update, delete). The table, its rows and its fields are not touched — this can be turned back on from the Schema tab.</p>
            </div>
            <Button disabled={busy} onClick={disable}>Disable</Button>
          </div>
        </div>
      )}
      <div className="card" style={{ padding: 14, borderColor: "var(--danger)" }}>
        <b>Delete table</b>
        <p className="muted">
          {resource
            ? <>Deletes the <span className="mono">{resource}</span> resource definition, then drops the <span className="mono">{table}</span> table itself. All of its rows are gone. This cannot be undone.</>
            : <>Drops the <span className="mono">{table}</span> table and all of its rows. This cannot be undone.</>}
        </p>
        {confirming ? (
          <div className="stack sm">
            <label className="row small muted" style={{ gap: 6 }}>Type {table} to confirm
              <input className="mono" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={table} />
            </label>
            <div className="row">
              <Button onClick={() => { setConfirming(false); setTyped(""); }}>Cancel</Button>
              <Button variant="danger" disabled={busy || typed !== table} onClick={dropTable}>Delete table</Button>
            </div>
          </div>
        ) : <div className="row"><Button variant="danger" onClick={() => setConfirming(true)}><Icon name="trash" size={14} />Delete table</Button></div>}
      </div>
    </div>
  );
}

function FilterButton({ columns, filter, onApply }) {
  const [draft, setDraft] = useState(filter || { column: columns[0] || "", op: "contains", value: "" });
  useEffect(() => { if (!draft.column && columns[0]) setDraft((d) => ({ ...d, column: columns[0] })); }, [columns]); // eslint-disable-line react-hooks/exhaustive-deps
  const needsValue = draft.op === "eq" || draft.op === "contains";
  return (
    <Popover
      label="Filter rows"
      width={320}
      align="end"
      trigger={(props) => <Button size="sm" className={filter ? "on" : ""} {...props}><Icon name="search" size={13} />{filter ? `${filter.column} ${filter.op === "contains" ? "∋" : filter.op === "eq" ? "=" : filter.op === "null" ? "is null" : "is not null"} ${filter.value || ""}`.trim() : "Filter"}</Button>}
    >
      {({ close }) => (
        <form className="tbl-pop" onSubmit={(e) => { e.preventDefault(); onApply(needsValue && !draft.value ? null : { ...draft }); close(); }}>
          <div className="pop-head"><span className="eyebrow">Filter rows</span></div>
          <div className="tbl-pop-body">
            <select value={draft.column} onChange={(e) => setDraft({ ...draft, column: e.target.value })} aria-label="Column">{columns.map((c) => <option key={c}>{c}</option>)}</select>
            <select value={draft.op} onChange={(e) => setDraft({ ...draft, op: e.target.value })} aria-label="Condition">
              <option value="contains">contains</option>
              <option value="eq">equals</option>
              <option value="null">is null</option>
              <option value="not_null">is not null</option>
            </select>
            {needsValue && <input value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })} placeholder="Value" aria-label="Value" />}
          </div>
          <div className="pop-foot tbl-pop-actions">
            {filter && <Button size="sm" onClick={() => { onApply(null); close(); }}>Clear</Button>}
            <Button size="sm" variant="primary" type="submit">Apply</Button>
          </div>
        </form>
      )}
    </Popover>
  );
}

function SortButton({ columns, sort, onApply }) {
  return (
    <Popover
      label="Sort rows"
      width={260}
      align="end"
      trigger={(props) => <Button size="sm" className={sort ? "on" : ""} {...props}><Icon name="chevronsUpDown" size={13} />{sort ? `${sort[0]} ${sort[1]}` : "Sort"}</Button>}
    >
      {({ close }) => (
        <>
          <div className="pop-head"><span className="eyebrow">Sort by</span>{sort && <button type="button" className="link-more" onClick={() => { onApply(null); close(); }}>Clear</button>}</div>
          <div className="pop-list" style={{ maxHeight: 320 }}>
            {columns.map((c) => (
              <div key={c} className="tbl-sort-row">
                <span className="mono grow">{c}</span>
                <button type="button" role="menuitem" className={sort?.[0] === c && sort[1] === "asc" ? "on" : ""} onClick={() => { onApply([c, "asc"]); close(); }}>Asc</button>
                <button type="button" role="menuitem" className={sort?.[0] === c && sort[1] === "desc" ? "on" : ""} onClick={() => { onApply([c, "desc"]); close(); }}>Desc</button>
              </div>
            ))}
          </div>
        </>
      )}
    </Popover>
  );
}

function RowSheet({ table, row, columns, types, onClose }) {
  const toast = useToast();
  const json = JSON.stringify(row, null, 2);
  return (
    <Sheet
      title={row.id != null ? `${table} · ${row.id}` : table}
      icon="resources"
      onClose={onClose}
      footer={<Button size="sm" onClick={async () => toast((await copyToClipboard(json)) ? "Row copied as JSON" : "Could not copy", "ok")}><Icon name="copy" size={14} /> Copy as JSON</Button>}
    >
      <dl className="tbl-row">
        {columns.map((name) => {
          const { text, kind } = display(row[name]);
          return (
            <div key={name}>
              <dt><span className="mono">{name}</span>{types[name]?.type && <small>{String(types[name].type).toLowerCase()}</small>}</dt>
              <dd className={kind}>{kind === "json" ? <pre>{JSON.stringify(asJson(row[name]) ?? row[name], null, 2)}</pre> : text}</dd>
            </div>
          );
        })}
      </dl>
    </Sheet>
  );
}
