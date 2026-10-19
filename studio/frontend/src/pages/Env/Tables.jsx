import { useEffect, useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { useDataSubnav } from "../../components/DataNav";
import { Icon } from "../../components/icons";
import { Badge, Button, Field, PageTabs, Popover, Sheet, Spinner, Switch, copyToClipboard, useAction, useToast } from "../../components/ui";
import { HistoryPanel, IndexesPanel, SchemaPanel } from "../../components/SchemaPanels";
import { ResourceForm, problem } from "../../components/definitions/forms";
import { useRefs } from "../../components/definitions/refs";
import { editable } from "../../lib/kinds";
import { del, envPath, get, patch, post, put, useApi } from "../../lib/api";

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
  const [deepLink] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return { table: params.get("table"), tab: normaliseView(params.get("tab") || "data") };
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
      <div className={`tbl-editor flex flex-col h-full min-h-0 bg-canvas [&.expanded]:fixed [&.expanded]:inset-0 [&.expanded]:z-[45] ${expanded ? "expanded" : ""}`}>
        <div className="tbl-tabs flex items-stretch h-10 shrink-0 [border-bottom:1px_solid_var(--line)] bg-head-bg overflow-x-auto [scrollbar-width:none] [&_.grow]:flex-1 [&_.popover-wrap]:items-center [&_.popover-wrap]:p-[0_4px]" role="tablist" aria-label="Open tables">
          {tabs.open.map((name) => (
            <div key={name} role="tab" aria-selected={tabs.active === name} className={`tbl-tab flex items-center max-w-[220px] shrink-0 [border-right:1px_solid_var(--line)] text-muted relative [&.active]:bg-canvas [&.active]:text-text [&:hover_.tbl-tab-close]:opacity-100 [&.active_.tbl-tab-close]:opacity-100 ${tabs.active === name ? "active" : ""}`}>
              <button type="button" className="tbl-tab-name flex items-center gap-[7px] min-w-0 h-full p-[0_4px_0_12px] border-0 bg-none text-inherit [font:500_12.5px_var(--mono)] cursor-pointer [&_span]:overflow-hidden [&_span]:text-ellipsis [&_span]:whitespace-nowrap" onClick={() => open(name)} title={name}><Icon name="resources" size={13} /><span>{name}</span></button>
              <button type="button" className="tbl-tab-close grid place-items-center w-[22px] h-[22px] mr-1.5 border-0 rounded-xs bg-none text-faint cursor-pointer opacity-0 [&:hover]:bg-hover [&:hover]:text-text" onClick={() => close(name)} aria-label={`Close ${name}`}><Icon name="x" size={12} /></button>
            </div>
          ))}
          <TablePicker tables={tables} onPick={open} />
          <span className="grow flex-1 min-w-0" />
          <button type="button" className="tbl-icon grid place-items-center w-[30px] h-[30px] m-[auto_4px] border-0 rounded-sm bg-none text-muted cursor-pointer [&:hover]:bg-hover [&:hover]:text-text" onClick={() => setExpanded(!expanded)} title={expanded ? "Exit full screen (Esc)" : "View full screen"} aria-label={expanded ? "Exit full screen" : "View full screen"}>
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
      trigger={(props) => <button type="button" className="tbl-icon grid place-items-center w-[30px] h-[30px] m-[auto_4px] border-0 rounded-sm bg-none text-muted cursor-pointer [&:hover]:bg-hover [&:hover]:text-text" title="Open a table in a new tab" aria-label="Open a table in a new tab" {...props}><Icon name="plus" size={15} /></button>}
    >
      {({ close }) => (
        <>
          <div className="pop-search flex items-center gap-2 m-[2px_8px_6px] p-[0_10px] h-control [border:1px_solid_var(--line-2)] rounded-sm text-faint [&:focus-within]:border-brand [&_input]:flex-1 [&_input]:min-w-0 [&_input]:h-full [&_input]:p-0 [&_input]:border-0 [&_input]:bg-transparent [&_input]:shadow-none! [&_input]:text-ui"><Icon name="search" size={14} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a table…" aria-label="Find a table" /></div>
          <div className="pop-list flex flex-col gap-px p-[2px_6px_6px] overflow-y-auto min-h-0" style={{ maxHeight: 360 }}>
            {shown.map((t) => (
              <button key={t.name} type="button" role="option" className="pop-item flex items-center gap-2.5 w-full min-h-10 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:outline-none [&.current]:bg-press [&:disabled]:opacity-50 [&:disabled]:cursor-default [&_b]:block [&_b]:font-medium-plus [&_b]:text-ui [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap [&_b]:normal-case [&.finding_b]:normal-case [&_small]:block [&_small]:text-muted [&_small]:text-xs [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap [&.finding_small]:whitespace-normal [&_.grow]:min-w-0 [&_.ctx-avatar]:w-[28px] [&_.ctx-avatar]:h-[28px] [&_.ctx-avatar]:text-xs [&.finding_small]:normal-case" onClick={() => { close(false); onPick(t.name); }}>
                <Icon name="resources" size={14} className="faint text-faint" />
                <span className="grow flex-1 min-w-0"><b className="mono font-mono text-ui-sm">{t.name}</b>{t.resource && <small>resource {t.resource}</small>}</span>
              </button>
            ))}
            {shown.length === 0 && <p className="pop-empty m-0 p-[14px_12px] text-muted text-ui-sm">No table matches.</p>}
          </div>
        </>
      )}
    </Popover>
  );
}

function Welcome({ tables, loading, onPick }) {
  return (
    <div className="tbl-welcome flex-1 flex flex-col items-center justify-center gap-2 p-10 text-center [&_h2]:text-title [&_h2]:font-semibold [&_p]:m-0 [&_p]:text-muted [&_p]:text-ui">
      <span className="tbl-welcome-icon grid place-items-center w-10 h-10 [border:1px_solid_var(--line-2)] rounded-sm text-muted"><Icon name="resources" size={20} /></span>
      <h2>Open a table</h2>
      <p>Pick a table on the left, or from the list below. Each one opens in its own tab.</p>
      {loading ? <Spinner /> : (
        <div className="tbl-welcome-list flex flex-wrap justify-center gap-1.5 max-w-[640px] mt-2.5 [&_button]:inline-flex [&_button]:items-center [&_button]:gap-1.5 [&_button]:h-[28px] [&_button]:p-[0_10px] [&_button]:[border:1px_solid_var(--line-2)] [&_button]:rounded-sm [&_button]:bg-none [&_button]:text-muted [&_button]:text-xs [&_button]:cursor-pointer [&_button:hover]:text-text [&_button:hover]:bg-hover">
          {tables.slice(0, 12).map((t) => (
            <button key={t.name} type="button" onClick={() => onPick(t.name)}><Icon name="resources" size={13} /><span className="mono font-mono text-ui-sm">{t.name}</span></button>
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

/** Schema became Definition, then folded into Columns: old deep links land on the tab that holds a table's columns. */
function normaliseView(view) {
  return view === "schema" || view === "definition" ? "columns" : view;
}

const VIEWS = [
  ["data", "Data", "database"],
  ["columns", "Columns", "columns"],
  ["indexes", "Indexes", "keys"],
  ["history", "History", "clock"],
  ["settings", "Settings", "settings"],
];

function TableTab({ env, base, table, resource, initialView = "data", onTablesChanged, onClose }) {
  const [view, setView] = useState(normaliseView(initialView));
  const info = useApi(`${base}/tables/${table}`);
  const columns = info.data?.columns || [];
  return (
    <div className="tbl-body flex flex-col flex-1 min-h-0 overflow-auto">
      <div className="tbl-identity flex items-center gap-2 min-w-0 h-10 shrink-0 p-[0_16px] [&_+_.page-tabs]:m-[0_16px_0] [&_+_.page-tabs]:pt-0 [&_+_.page-tabs]:[border-bottom:1px_solid_var(--line)]">
        <b className="mono font-mono text-ui-sm tbl-title text-body">{table}</b>
        <span className="faint text-faint small text-ui-sm">{info.data ? `${(info.data.rows ?? 0).toLocaleString()} rows · ${columns.length} columns` : "…"}</span>
        {resource && <Badge tone="brand">resource <b>{resource}</b></Badge>}
      </div>
      <PageTabs
        tabs={VIEWS.map(([value, label, icon]) => ({ value, label: <><Icon name={icon} size={14} />{label}</> }))}
        value={view}
        onChange={setView}
      />
      {view === "data" && <DataView env={env} base={base} table={table} columns={columns} info={info} resource={resource} />}
      {view === "columns" && (
        resource
          ? <ResourceColumns env={env} resource={resource} onRowsChanged={info.reload} onTablesChanged={onTablesChanged} />
          : <UnclaimedSchema env={env} table={table} columns={columns} onClaimed={() => onTablesChanged?.()} />
      )}
      {view === "indexes" && (resource
        ? <div className="resource-columns p-[0_var(--gutter)_48px] [&_.stack.lg]:gap-0 [&_.card]:p-[24px_0_28px] [&_.card]:border-0 [&_.card]:rounded-none [&_.card]:[border-top:1px_solid_var(--line)] [&_.card]:bg-transparent [&_.card_>_.spread:first-child]:mb-4! [&_.card_.card-head]:min-h-0 [&_.card_.card-head]:p-[0_0_16px] [&_.card_.card-head]:border-b-0 [&_.schema-change]:border-line-2 [&_.schema-change]:rounded-none [&_.schema-change]:bg-transparent [&_.schema-change_+_.schema-change]:border-t-0 [&_.schema-entry]:rounded-none max-mobile:p-[0_16px_32px]"><div className="resource-columns-inner w-[min(100%,_720px)] m-[0_auto]"><IndexesPanel env={env} name={resource} onChanged={info.reload} /></div></div>
        : <div className="tbl-state flex items-center justify-center gap-2 p-[48px_16px] text-muted text-ui">Define this table as a resource before managing its indexes.</div>
      )}
      {view === "history" && (resource
        ? <div style={{ padding: 16 }}><HistoryPanel env={env} name={resource} onChanged={info.reload} /></div>
        : <div className="tbl-state flex items-center justify-center gap-2 p-[48px_16px] text-muted text-ui">No resource defined for this table yet — define one from the Columns tab to track changes.</div>
      )}
      {view === "settings" && (resource
        ? <ResourceSettings env={env} table={table} resource={resource} onRowsChanged={info.reload} onTablesChanged={onTablesChanged} onDeleted={onClose} />
        : <TableSettings env={env} table={table} resource={resource} onDeleted={onClose} onDisabled={onTablesChanged} />
      )}
    </div>
  );
}

function DataView({ env, base, table, columns, info, resource }) {
  const [page, setPage] = useState({ offset: 0, size: 100 });
  const [sort, setSort] = useState(null); // [column, "asc" | "desc"]
  const [filter, setFilter] = useState(null); // { column, op, value }
  const [row, setRow] = useState(null);
  const [creating, setCreating] = useState(false);
  const [newValues, setNewValues] = useState({});
  const [createError, setCreateError] = useState(null);
  const [createBusy, setCreateBusy] = useState(false);
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
  const pkCol = columns.find((name) => types[name]?.primary_key);
  const createRow = async () => {
    setCreateError(null);
    setCreateBusy(true);
    try {
      const body = {};
      for (const name of columns) {
        const v = newValues[name];
        if (v === "" || v === null || v === undefined) continue;
        if (pkCol && name === pkCol) continue; // backend auto-generates the primary key
        body[name] = v;
      }
      await post(envPath(env, `/resources/${resource}/records`), body);
      setCreating(false);
      setNewValues({});
      rows.reload();
      info.reload();
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setCreateBusy(false);
    }
  };
  return (
    <>
      <div className="tbl-toolbar flex items-center justify-between gap-3 h-12 shrink-0 p-[0_16px] [border-bottom:1px_solid_var(--line)]">
        <span className="grow flex-1 min-w-0" />
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}>
          {resource && (
            <Button size="sm" variant="primary" onClick={() => { setNewValues({}); setCreateError(null); setCreating(true); }}><Icon name="plus" size={13} /> Add row</Button>
          )}
          <FilterButton columns={names} filter={filter} onApply={(next) => { setFilter(next); setPage((p) => ({ ...p, offset: 0 })); }} />
          <SortButton columns={names} sort={sort} onApply={(next) => { setSort(next); setPage((p) => ({ ...p, offset: 0 })); }} />
          <Button size="sm" onClick={() => { info.reload(); rows.reload(); }} title="Reload rows"><Icon name="refresh" size={14} /></Button>
        </div>
      </div>

      <div className="tbl-grid-wrap relative flex-1 min-h-0 overflow-auto">
        {rows.error ? <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error" style={{ margin: 16 }}>{rows.error.message}</div> : (
          <table className="tbl-grid w-[max-content] min-w-[100%] border-separate border-spacing-0 text-ui-sm [&_th]:h-[34px] [&_th]:p-[0_12px] [&_th]:[border-bottom:1px_solid_var(--line)] [&_th]:[border-right:1px_solid_var(--line)] [&_th]:whitespace-nowrap [&_th]:text-left [&_td]:h-[34px] [&_td]:p-[0_12px] [&_td]:[border-bottom:1px_solid_var(--line)] [&_td]:[border-right:1px_solid_var(--line)] [&_td]:whitespace-nowrap [&_td]:text-left [&_th]:sticky [&_th]:top-0 [&_th]:z-[2] [&_th]:bg-head-bg [&_th]:text-text [&_th]:text-xs [&_th]:font-medium-plus [&_th]:normal-case [&_th]:cursor-pointer [&_th]:select-none [&_th]:shadow-none [&_th::first-letter]:normal-case [&_th:hover]:bg-hover [&_th.sorted]:text-brand-strong [&_.tbl-num]:sticky [&_.tbl-num]:left-0 [&_.tbl-num]:z-[1] [&_.tbl-num]:w-12 [&_.tbl-num]:min-w-12 [&_.tbl-num]:p-[0_10px] [&_.tbl-num]:bg-canvas [&_.tbl-num]:text-faint [&_.tbl-num]:[font:11px_var(--mono)] [&_.tbl-num]:text-right [&_th.tbl-num]:z-[3] [&_th.tbl-num]:bg-head-bg [&_th.tbl-num]:cursor-default [&_tbody_tr]:cursor-pointer [&_tbody_tr:hover_td]:bg-hover [&_tbody_tr:hover_td.tbl-num]:bg-[color-mix(in_srgb,_var(--canvas)_92%,_var(--text))]">
            <thead>
              <tr>
                <th className="tbl-num">#</th>
                {names.map((name) => {
                  const c = types[name];
                  const sorted = sort && sort[0] === name ? sort[1] : null;
                  return (
                    <th key={name} onClick={() => toggleSort(name)} className={sorted ? "sorted" : ""} title={`Sort by ${name}`}>
                      <span className="tbl-th inline-flex items-center gap-1.5">
                        {c?.primary_key && <Icon name="keys" size={11} className="tbl-pk text-warn" />}
                        <span className="tbl-th-name font-mono">{name}</span>
                        {c?.type && <span className="tbl-th-type [font:500_10.5px_var(--mono)] text-faint">{String(c.type).toLowerCase()}</span>}
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
                    return <td key={name} className={`tbl-cell max-w-[320px] overflow-hidden text-ellipsis font-mono text-xs [&.null]:text-faint [&.null]:italic [&.null]:text-2xs [&.num]:text-right [&.num]:text-info [&.bool]:text-warn [&.json]:text-muted ${kind}`} title={text.length > 40 ? text : undefined}>{text}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rows.loading && !rows.data && <div className="tbl-state flex items-center justify-center gap-2 p-[48px_16px] text-muted text-ui"><Spinner /> Loading rows…</div>}
        {rows.data && data.length === 0 && <div className="tbl-state flex items-center justify-center gap-2 p-[48px_16px] text-muted text-ui">{filter ? "No rows match this filter." : "This table is empty."}</div>}
      </div>

      <div className="tbl-footer flex items-center justify-between gap-3 h-[44px] shrink-0 p-[0_16px] [border-top:1px_solid_var(--line)] text-muted text-ui-sm [&_.spinner]:inline-block [&_.spinner]:w-[11px] [&_.spinner]:h-[11px] [&_.spinner]:[vertical-align:-1px]">
        <span>{data.length ? `Rows ${(page.offset + 1).toLocaleString()}–${last.toLocaleString()}${total != null ? ` of ${total.toLocaleString()}` : ""}` : "No rows"}{filter && <> · filtered</>}{rows.loading && rows.data && <> · <Spinner /></>}</span>
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 8 }}>
          <label className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 small text-ui-sm muted text-muted" style={{ gap: 6 }}>Rows per page
            <select value={page.size} onChange={(e) => setPage({ offset: 0, size: Number(e.target.value) })} style={{ width: 80, height: 28 }}>
              {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <div className="tbl-pager inline-flex items-center [border:1px_solid_var(--line-2)] rounded-sm overflow-hidden h-[28px] [&_button]:grid [&_button]:grid-flow-col [&_button]:place-items-center [&_button]:h-full [&_button]:p-[0_8px] [&_button]:border-0 [&_button]:[border-left:1px_solid_var(--line-2)] [&_button]:bg-none [&_button]:text-text [&_button]:cursor-pointer [&_button:first-child]:border-l-0 [&_button:disabled]:text-faint [&_button:disabled]:cursor-default [&_button:not(:disabled):hover]:bg-hover [&_.tbl-page_+_button]:[border-left:1px_solid_var(--line-2)]">
            <button type="button" disabled={page.offset === 0} onClick={() => setPage((p) => ({ ...p, offset: 0 }))} aria-label="First page"><Icon name="chevronRight" size={13} className="rotate-180 transform-[rotate(180deg)]" /><Icon name="chevronRight" size={13} className="rotate-180 transform-[rotate(180deg)]" style={{ marginLeft: -8 }} /></button>
            <button type="button" disabled={page.offset === 0} onClick={() => setPage((p) => ({ ...p, offset: Math.max(0, p.offset - p.size) }))} aria-label="Previous page"><Icon name="chevronRight" size={13} className="rotate-180 transform-[rotate(180deg)]" /></button>
            <span className="tbl-page p-[0_10px] [border-left:1px_solid_var(--line-2)] h-full grid place-items-center text-xs text-muted whitespace-nowrap">Page {Math.floor(page.offset / page.size) + 1}{total != null ? ` of ${Math.max(1, Math.ceil(total / page.size))}` : ""}</span>
            <button type="button" disabled={data.length < page.size || (total != null && last >= total)} onClick={() => setPage((p) => ({ ...p, offset: p.offset + p.size }))} aria-label="Next page"><Icon name="chevronRight" size={13} /></button>
          </div>
        </div>
      </div>

      {creating && (
        <Sheet title={`Add row to ${table}`} onClose={() => { setCreating(false); setCreateError(null); setNewValues({}); }} footer={
          <>
            <Button onClick={() => { setCreating(false); setCreateError(null); setNewValues({}); }}>Cancel</Button>
            <Button variant="primary" disabled={createBusy} onClick={createRow}>{createBusy ? "Creating…" : "Create row"}</Button>
          </>
        }>
          {createError && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{createError}</div>}
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm" style={{ gap: 12 }}>
            {names.map((name) => {
              const c = types[name];
              return (
                <Field key={name} label={name} hint={c?.type ? String(c.type).toLowerCase() : undefined} optional={!c?.primary_key}>
                  <input className="mono font-mono text-ui-sm" value={newValues[name] ?? ""} onChange={(e) => setNewValues({ ...newValues, [name]: e.target.value || null })} placeholder={c?.primary_key ? "auto-generated" : undefined} />
                </Field>
              );
            })}
        </div>
      </Sheet>
      )}

      {row && <RowSheet table={table} row={row} columns={names} types={types} env={env} resource={resource} onClose={() => setRow(null)} onUpdated={() => { rows.reload(); info.reload(); setRow(null); }} />}
    </>
  );
}

/** A resource-backed table's Columns tab: the table's headings — their names, types, constraints and relations — plus
 *  the schema changes they imply and their index panel, reusing the editors built for the Resources page. */
function ResourceColumns({ env, resource, onRowsChanged, onTablesChanged }) {
  const path = envPath(env, `/resources/${resource}`);
  const item = useApi(path);
  const refs = useRefs(env);
  const [draft, setDraft] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [run, busy] = useAction();
  useEffect(() => { if (item.data) { const next = editable(item.data); setDraft(next); setBaseline(next); } }, [item.data]);
  if (!item.data || !draft) return <div className="tbl-state flex items-center justify-center gap-2 p-[48px_16px] text-muted text-ui"><Spinner /></div>;
  const issue = problem("resources", draft);
  const dirty = !!baseline && JSON.stringify(draft) !== JSON.stringify(baseline);
  const save = async () => {
    const result = await run(() => put(path, draft), "Columns saved");
    if (result) { item.reload(); onRowsChanged?.(); }
  };
  return (
    <div className="resource-columns p-[0_var(--gutter)_48px] [&_.stack.lg]:gap-0 [&_.card]:p-[24px_0_28px] [&_.card]:border-0 [&_.card]:rounded-none [&_.card]:[border-top:1px_solid_var(--line)] [&_.card]:bg-transparent [&_.card_>_.spread:first-child]:mb-4! [&_.card_.card-head]:min-h-0 [&_.card_.card-head]:p-[0_0_16px] [&_.card_.card-head]:border-b-0 [&_.schema-change]:border-line-2 [&_.schema-change]:rounded-none [&_.schema-change]:bg-transparent [&_.schema-change_+_.schema-change]:border-t-0 [&_.schema-entry]:rounded-none max-mobile:p-[0_16px_32px]">
      <div className="resource-columns-inner w-[min(100%,_720px)] m-[0_auto]">
        <div className="resource-columns-head flex items-center justify-between gap-4 min-h-[68px] [border-top:1px_solid_var(--line)] [&_>_div:first-child]:flex [&_>_div:first-child]:flex-col [&_>_div:first-child]:gap-0.5 [&_>_div:first-child_>_b]:text-base [&_>_div:first-child_>_b]:font-semibold max-mobile:items-start max-mobile:flex-col max-mobile:justify-center max-mobile:p-[14px_0]">
          <div><b>Columns</b><span className="muted text-muted">The columns and relations behind <b className="mono font-mono text-ui-sm">{resource}</b>.</span></div>
          <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
            <span className="hint text-xs font-book text-muted leading-[1.45]">{issue}</span>
            {dirty && <span className="dirty-pill p-[3px_9px] [border:1px_solid_var(--brand)] rounded-full bg-brand-soft text-brand-strong text-caption font-semibold whitespace-nowrap">Unsaved changes</span>}
            <Button size="sm" variant="primary" disabled={busy || !!issue} onClick={save}>Save columns</Button>
          </div>
        </div>
        <div className="resource-columns-form [&_.form-section]:p-[26px_0_30px] [&_.form-section]:border-0 [&_.form-section]:[border-top:1px_solid_var(--line)] [&_.form-section]:rounded-none [&_.form-section]:bg-transparent [&_.form-section:last-child]:border-b-0 [&_.field-card]:bg-canvas">
          <ResourceForm body={draft} patch={(changes) => setDraft((current) => ({ ...current, ...changes }))} refs={refs} isNew={false} mode="columns" />
        </div>
        <SchemaPanel env={env} name={resource} onChanged={() => { item.reload(); onRowsChanged?.(); }} onAdopted={() => item.reload()} />
      </div>
    </div>
  );
}

function ResourceSettings({ env, table, resource, onRowsChanged, onTablesChanged, onDeleted }) {
  const path = envPath(env, `/resources/${resource}`);
  const item = useApi(path);
  const refs = useRefs(env);
  const [draft, setDraft] = useState(null);
  const [run, busy] = useAction();
  useEffect(() => { if (item.data) setDraft(editable(item.data)); }, [item.data]);
  if (!item.data || !draft) return <div className="tbl-state flex items-center justify-center gap-2 p-[48px_16px] text-muted text-ui"><Spinner /></div>;
  const issue = problem("resources", draft);
  const save = async () => {
    const result = await run(() => put(path, draft), "Settings saved");
    if (result) { item.reload(); onRowsChanged?.(); onTablesChanged?.(); }
  };
  return (
    <div className="resource-settings flex-none w-full min-h-[min-content] p-[0_var(--gutter)_48px] max-mobile:p-[0_16px_32px]">
      <div className="resource-settings-inner w-[min(100%,_720px)] m-[0_auto]">
        <div className="resource-settings-head flex items-center justify-between gap-4 min-h-[68px] [border-top:1px_solid_var(--line)] [&_>_div:first-child]:flex [&_>_div:first-child]:flex-col [&_>_div:first-child]:gap-0.5 [&_>_div:first-child_>_b]:text-base [&_>_div:first-child_>_b]:font-semibold max-mobile:items-start max-mobile:flex-col max-mobile:justify-center max-mobile:p-[14px_0]">
          <div><b>Settings</b><span className="muted text-muted">Configure access, behaviour, limits and resource details.</span></div>
          <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><span className="hint text-xs font-book text-muted leading-[1.45]">{issue}</span><Button size="sm" variant="primary" disabled={busy || !!issue} onClick={save}>Save settings</Button></div>
        </div>
        <div className="resource-settings-form [&_.form-section]:p-[26px_0_30px] [&_.form-section]:border-0 [&_.form-section]:[border-top:1px_solid_var(--line)] [&_.form-section]:rounded-none [&_.form-section]:bg-transparent [&_.form-grid]:flex [&_.form-grid]:flex-col [&_.form-grid]:gap-0 [&_.form-grid_>_*]:w-full [&_.form-grid_>_*]:p-[14px_0] [&_.form-grid_>_*]:[border-bottom:1px_solid_var(--line)] [&_.form-grid_>_:first-child]:pt-0 [&_.form-grid_>_:last-child]:pb-0 [&_.form-grid_>_:last-child]:border-b-0 [&_.switch]:min-h-10 [&_.switch]:items-center [&_.switch_.switch-text]:min-w-0">
          <ResourceForm body={draft} patch={(changes) => setDraft((current) => current ? { ...current, ...changes } : current)} refs={refs} isNew={false} mode="settings" />
        </div>
        <TableSettings env={env} table={table} resource={resource} onDeleted={onDeleted} onDisabled={onTablesChanged} embedded />
      </div>
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
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ padding: 16 }}>
      <p className="muted text-muted">This table has no resource definition, so it has no REST API, access control or schema history — just the raw columns below.</p>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 6 }}>
        {columns.map((c) => <Badge key={c.name}>{c.name} · {String(c.type).toLowerCase()}</Badge>)}
      </div>
      {suggestion.loading ? <Spinner /> : found ? (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
          <label className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 small text-ui-sm muted text-muted" style={{ gap: 6 }}>Resource name
            <input className="mono font-mono text-ui-sm" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 200 }} />
          </label>
          {found.problem && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] warn">{found.problem}</div>}
          {found.skipped.length > 0 && <span className="hint text-xs font-book text-muted leading-[1.45]">Left out (not usable as field names): {found.skipped.join(", ")}</span>}
          <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><Button variant="primary" disabled={busy || !name.trim()} onClick={claim}><Icon name="database" size={14} />Define a resource</Button></div>
        </div>
      ) : <span className="hint text-xs font-book text-muted leading-[1.45]">Reload the Columns tab once the table has finished loading to define a resource from it.</span>}
    </div>
  );
}

/** Settings: pause the resource's REST API without touching its data, or remove the table for good. */
function TableSettings({ env, table, resource, onDeleted, onDisabled, embedded = false }) {
  const [run, busy] = useAction();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [realtime, setRealtime] = useState(null);
  const disable = async () => {
    const path = envPath(env, `/resources/${resource}`);
    const result = await run(async () => {
      const current = await get(path);
      const operations = Object.fromEntries(Object.entries(current.operations || {}).map(([op, setting]) => [op, { ...setting, enabled: false }]));
      return put(path, { ...editable(current), operations });
    }, "The resource's REST API is now off. Fields, history and the table are untouched.");
    if (result) onDisabled?.();
  };
  const toggleRealtime = async (value) => {
    const path = envPath(env, `/resources/${resource}`);
    const result = await run(async () => {
      const current = await get(path);
      return put(path, { ...editable(current), realtime: value });
    }, value ? "Broadcasting row changes to realtime subscribers." : "Stopped broadcasting row changes.");
    if (result) setRealtime(value);
  };
  const dropTable = async () => {
    const result = await run(async () => {
      if (resource) await del(envPath(env, `/resources/${resource}`));
      await del(envPath(env, `/database/tables/${table}`));
    }, `${table} is gone`);
    if (result) onDeleted?.();
  };
  useEffect(() => {
    if (!resource) return;
    let cancelled = false;
    get(envPath(env, `/resources/${resource}`)).then((current) => { if (!cancelled) setRealtime(Boolean(current.realtime)); });
    return () => { cancelled = true; };
  }, [env, resource]);
  return (
    <div className={embedded ? "resource-settings-operations" : "stack"} style={embedded ? undefined : { padding: 16, maxWidth: 560 }}>
      {resource && (
        <>
          <div className="card bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-md p-4 [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [&.flush_.card-head_+_*]:[border-top:1px_solid_var(--line)] [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:transform-[none]! [a&:hover]:shadow-none [border:1px_solid_var(--line)] shadow-none [&_>_.spread:first-child]:mb-3! [&_>_.spread_h2]:text-base [&_>_.spread_h2]:font-semibold [&_>_.spread_h2]:tracking-[-0.01em] [&.flush_>_.card-head_+_*]:border-t-0 [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&_>_.spread_.chips]:gap-0 [&_>_.spread_.chips]:flex-nowrap [&_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_>_.spread_.chips]:rounded-sm [&_>_.spread_.chips]:overflow-hidden [&_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_>_.spread_.chip]:p-[0_10px] [&_>_.spread_.chip]:border-0 [&_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_>_.spread_.chip]:rounded-none [&_>_.spread_.chip]:bg-transparent [&_>_.spread_.chip]:text-muted [&_>_.spread_.chip]:text-xs [&_>_.spread_.chip]:font-medium-plus [&_>_.spread_.chip:first-child]:border-l-0 [&_>_.spread_.chip.active]:bg-press [&_>_.spread_.chip.active]:text-text [&_>_.spread_.chip.on]:bg-press [&_>_.spread_.chip.on]:text-text [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100" style={{ padding: 14 }}>
            <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ justifyContent: "space-between" }}>
              <div>
                <b>Disable the API</b>
                <p className="muted text-muted">Turns off every operation on <span className="mono font-mono text-ui-sm">{resource}</span> (list, get, create, update, delete). The table, its rows and its fields are not touched — this can be turned back on from Settings.</p>
              </div>
              <Button disabled={busy} onClick={disable}>Disable</Button>
            </div>
          </div>
          <div className="card bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-md p-4 [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [&.flush_.card-head_+_*]:[border-top:1px_solid_var(--line)] [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:transform-[none]! [a&:hover]:shadow-none [border:1px_solid_var(--line)] shadow-none [&_>_.spread:first-child]:mb-3! [&_>_.spread_h2]:text-base [&_>_.spread_h2]:font-semibold [&_>_.spread_h2]:tracking-[-0.01em] [&.flush_>_.card-head_+_*]:border-t-0 [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&_>_.spread_.chips]:gap-0 [&_>_.spread_.chips]:flex-nowrap [&_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_>_.spread_.chips]:rounded-sm [&_>_.spread_.chips]:overflow-hidden [&_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_>_.spread_.chip]:p-[0_10px] [&_>_.spread_.chip]:border-0 [&_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_>_.spread_.chip]:rounded-none [&_>_.spread_.chip]:bg-transparent [&_>_.spread_.chip]:text-muted [&_>_.spread_.chip]:text-xs [&_>_.spread_.chip]:font-medium-plus [&_>_.spread_.chip:first-child]:border-l-0 [&_>_.spread_.chip.active]:bg-press [&_>_.spread_.chip.active]:text-text [&_>_.spread_.chip.on]:bg-press [&_>_.spread_.chip.on]:text-text [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100" style={{ padding: 14 }}>
            <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ justifyContent: "space-between" }}>
              <div>
                <b>Broadcast changes</b>
                <p className="muted text-muted">Push row changes (create, update, delete) to realtime subscribers on the <span className="mono font-mono text-ui-sm">resource:{resource}</span> channel.</p>
              </div>
              <Switch size="sm" checked={realtime ?? false} onChange={toggleRealtime} disabled={busy} />
            </div>
          </div>
        </>
      )}
      <div className="card bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-md p-4 [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [&.flush_.card-head_+_*]:[border-top:1px_solid_var(--line)] [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:transform-[none]! [a&:hover]:shadow-none [border:1px_solid_var(--line)] shadow-none [&_>_.spread:first-child]:mb-3! [&_>_.spread_h2]:text-base [&_>_.spread_h2]:font-semibold [&_>_.spread_h2]:tracking-[-0.01em] [&.flush_>_.card-head_+_*]:border-t-0 [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&_>_.spread_.chips]:gap-0 [&_>_.spread_.chips]:flex-nowrap [&_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_>_.spread_.chips]:rounded-sm [&_>_.spread_.chips]:overflow-hidden [&_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_>_.spread_.chip]:p-[0_10px] [&_>_.spread_.chip]:border-0 [&_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_>_.spread_.chip]:rounded-none [&_>_.spread_.chip]:bg-transparent [&_>_.spread_.chip]:text-muted [&_>_.spread_.chip]:text-xs [&_>_.spread_.chip]:font-medium-plus [&_>_.spread_.chip:first-child]:border-l-0 [&_>_.spread_.chip.active]:bg-press [&_>_.spread_.chip.active]:text-text [&_>_.spread_.chip.on]:bg-press [&_>_.spread_.chip.on]:text-text [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100" style={{ padding: 14, borderColor: "var(--danger)" }}>
        <b>Delete table</b>
        <p className="muted text-muted">
          {resource
            ? <>Deletes the <span className="mono font-mono text-ui-sm">{resource}</span> resource definition, then drops the <span className="mono font-mono text-ui-sm">{table}</span> table itself. All of its rows are gone. This cannot be undone.</>
            : <>Drops the <span className="mono font-mono text-ui-sm">{table}</span> table and all of its rows. This cannot be undone.</>}
        </p>
        {confirming ? (
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
            <label className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 small text-ui-sm muted text-muted" style={{ gap: 6 }}>Type {table} to confirm
              <input className="mono font-mono text-ui-sm" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={table} />
            </label>
            <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
              <Button onClick={() => { setConfirming(false); setTyped(""); }}>Cancel</Button>
              <Button variant="danger" disabled={busy || typed !== table} onClick={dropTable}>Delete table</Button>
            </div>
          </div>
        ) : <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><Button variant="danger" onClick={() => setConfirming(true)}><Icon name="trash" size={14} />Delete table</Button></div>}
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
          <div className="pop-head flex items-center justify-between gap-2 p-[10px_12px_6px] [&_.eyebrow]:text-2xs [&_.eyebrow]:tracking-[0.06em]"><span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Filter rows</span></div>
          <div className="tbl-pop-body flex flex-col gap-2 p-[4px_12px_12px]">
            <select value={draft.column} onChange={(e) => setDraft({ ...draft, column: e.target.value })} aria-label="Column">{columns.map((c) => <option key={c}>{c}</option>)}</select>
            <select value={draft.op} onChange={(e) => setDraft({ ...draft, op: e.target.value })} aria-label="Condition">
              <option value="contains">contains</option>
              <option value="eq">equals</option>
              <option value="null">is null</option>
              <option value="not_null">is not null</option>
            </select>
            {needsValue && <input value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })} placeholder="Value" aria-label="Value" />}
          </div>
          <div className="pop-foot flex flex-col gap-px p-1.5 [border-top:1px_solid_var(--line)] tbl-pop-actions flex-row justify-end gap-1.5">
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
          <div className="pop-head flex items-center justify-between gap-2 p-[10px_12px_6px] [&_.eyebrow]:text-2xs [&_.eyebrow]:tracking-[0.06em]"><span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Sort by</span>{sort && <button type="button" className="link-more inline-flex items-center gap-0.5 text-ui-sm font-semibold text-muted [&:hover]:text-text" onClick={() => { onApply(null); close(); }}>Clear</button>}</div>
          <div className="pop-list flex flex-col gap-px p-[2px_6px_6px] overflow-y-auto min-h-0" style={{ maxHeight: 320 }}>
            {columns.map((c) => (
              <div key={c} className="tbl-sort-row flex items-center gap-1 p-[4px_6px] rounded-xs text-ui-sm [&:hover]:bg-hover [&_button]:h-6 [&_button]:p-[0_8px] [&_button]:[border:1px_solid_var(--line-2)] [&_button]:rounded-xs [&_button]:bg-none [&_button]:text-muted [&_button]:text-caption [&_button]:cursor-pointer [&_button:hover]:text-text [&_button:hover]:border-brand [&_button.on]:text-text [&_button.on]:border-brand">
                <span className="mono font-mono text-ui-sm grow flex-1 min-w-0">{c}</span>
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

function RowSheet({ table, row, columns, types, env, resource, onClose, onUpdated }) {
  const toast = useToast();
  const json = JSON.stringify(row, null, 2);
  const [editing, setEditing] = useState(false);
  const [editValues, setEditValues] = useState(row);
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const pkCol = columns.find((name) => types[name]?.primary_key);
  const saveRow = async () => {
    setSaveError(null);
    setSaveBusy(true);
    try {
      const body = {};
      for (const name of columns) {
        const v = editValues[name];
        if (v === "" || v === null || v === undefined) continue;
        if (pkCol && name === pkCol) continue;
        body[name] = v;
      }
      await patch(envPath(env, `/resources/${resource}/records/${row.id}`), body);
      setEditing(false);
      onUpdated?.();
      toast("Row saved", "ok");
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaveBusy(false);
    }
  };
  return (
    <Sheet
      title={row.id != null ? `${table} · ${row.id}` : table}
      icon="resources"
      onClose={onClose}
      footer={
        <>
          {editing ? (
            <>
              <Button onClick={() => { setEditing(false); setEditValues(row); setSaveError(null); }}>Cancel</Button>
              <Button variant="primary" disabled={saveBusy} onClick={saveRow}>{saveBusy ? "Saving…" : "Save changes"}</Button>
            </>
          ) : (
            <>
              <Button size="sm" onClick={async () => toast((await copyToClipboard(json)) ? "Row copied as JSON" : "Could not copy", "ok")}><Icon name="copy" size={14} /> Copy as JSON</Button>
              <Button size="sm" variant="primary" onClick={() => { setEditValues({ ...row }); setEditing(true); setSaveError(null); }}><Icon name="edit" size={14} /> Edit</Button>
            </>
          )}
        </>
      }
    >
      {editing && saveError && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{saveError}</div>}
      <dl className="tbl-row m-0 flex flex-col [&_>_div]:grid [&_>_div]:grid-cols-[180px_minmax(0,_1fr)] [&_>_div]:gap-3 [&_>_div]:p-[10px_0] [&_>_div]:[border-bottom:1px_solid_var(--line)] [&_dt]:flex [&_dt]:flex-col [&_dt]:gap-0.5 [&_dt]:text-muted [&_dt]:text-ui-sm [&_dt_small]:text-faint [&_dt_small]:[font:10.5px_var(--mono)] [&_dd]:m-0 [&_dd]:[font:12.5px_var(--mono)] [&_dd]:wrap-anywhere [&_dd.null]:text-faint [&_dd.null]:italic [&_dd_pre]:m-0 [&_dd_pre]:p-[8px_10px] [&_dd_pre]:[border:1px_solid_var(--line)] [&_dd_pre]:rounded-sm [&_dd_pre]:bg-head-bg [&_dd_pre]:text-xs [&_dd_pre]:whitespace-pre-wrap">
        {columns.map((name) => {
          const c = types[name];
          const value = editValues[name];
          const { text, kind } = display(value);
          if (editing) {
            return (
              <div key={name} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <dt><span className="mono font-mono text-ui-sm">{name}</span>{c?.type && <small>{String(c.type).toLowerCase()}</small>}</dt>
                <input className="mono font-mono text-ui-sm" value={value == null ? "" : String(value)} onChange={(e) => setEditValues({ ...editValues, [name]: e.target.value || null })} placeholder={c?.primary_key ? "auto-generated" : undefined} />
              </div>
            );
          }
          return (
            <div key={name}>
              <dt><span className="mono font-mono text-ui-sm">{name}</span>{c?.type && <small>{String(c.type).toLowerCase()}</small>}</dt>
              <dd className={kind}>{kind === "json" ? <pre>{JSON.stringify(asJson(value) ?? value, null, 2)}</pre> : text}</dd>
            </div>
          );
        })}
      </dl>
    </Sheet>
  );
}
