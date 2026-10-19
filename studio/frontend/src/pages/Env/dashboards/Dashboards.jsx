import { useEffect, useState } from "react";
import { Icon } from "../../../components/icons";
import { Badge, Button, Card, CopyText, EmptyState, Field, Loading, PageHead, Sheet, Switch, Table, useAction } from "../../../components/ui";
import { del, envPath, patch, post, put, useApi } from "../../../lib/api";

const WIDGET_TYPES = [
  ["metric", "Metric", "A single number — count, sum, average, minimum or maximum."],
  ["table", "Table", "Rows from a resource, with optional columns, filters and sorting."],
  ["chart", "Chart", "A bar or line chart: one value per group."],
  ["kanban", "Kanban board", "Cards grouped by a field; dragging a card changes it."],
  ["form", "Form", "Create or update one record."],
];
const AGGREGATES = [["count", "Count"], ["sum", "Sum"], ["avg", "Average"], ["min", "Minimum"], ["max", "Maximum"]];
const FILTER_OPS = [["eq", "is"], ["neq", "is not"], ["gt", "is greater than"], ["gte", "is at least"], ["lt", "is less than"], ["lte", "is at most"], ["like", "matches"], ["ilike", "matches (any case)"]];
const WIDTHS = [["full", "Full width"], ["half", "Half width"], ["third", "A third"]];

const uid = (prefix) => `${prefix}-${Math.random().toString(36).slice(2, 9)}`;
const blankPage = (n) => ({ id: uid("page"), name: `Page ${n}`, path: n === 1 ? "/" : `/page-${n}`, widgets: [] });
const blankWidget = () => ({ id: uid("widget"), type: "metric", title: "", width: "full", binding: { source: "resource", resource: "", aggregate: "count", filters: [] } });

export function Dashboards({ env }) {
  const list = useApi(envPath(env, "/dashboards"));
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [run, busy] = useAction();
  const reload = () => list.reload();
  return (
    <>
      <PageHead title="Dashboards" description="Pages of metrics, tables, kanban boards and forms, published as their own web page. Every read and write goes through the normal REST API, so each resource's own policies decide what a viewer can do." actions={<Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" />New dashboard</Button>} />
      <Card flush>
        <Loading state={list} empty={
          <EmptyState icon="layers" title="No dashboards yet" action={<Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" />New dashboard</Button>}>
            Build one from your resources — a metric strip, a table, a kanban board, or a form — and publish it as its own page.
          </EmptyState>
        }>
          {(data) => (
            <Table
              rows={data.data}
              onRowClick={(row) => setEditing(row)}
              columns={[
                { label: "Dashboard", render: (d) => <div><b>{d.title || d.name}</b><div className="hint text-xs font-book text-muted leading-[1.45] mono font-mono text-ui-sm">{d.name}</div></div> },
                { label: "Pages", render: (d) => (d.definition?.pages || []).length },
                { label: "Status", render: (d) => d.published ? <Badge tone={d.public ? "green" : undefined}>{d.public ? "published" : "unpublished"}</Badge> : <Badge>draft</Badge> },
                { label: "Enabled", render: (d) => (d.enabled ? "Yes" : "No") },
                { label: "", render: (d) => <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" onClick={(e) => e.stopPropagation()}><Button size="sm" variant="danger" disabled={busy} onClick={async () => { if (confirm(`Delete "${d.title || d.name}"?`) && await run(() => del(envPath(env, `/dashboards/${d.id}`)), "Deleted")) reload(); }}>Delete</Button></div> },
              ]}
            />
          )}
        </Loading>
      </Card>
      {creating && (
        <NewDashboardSheet
          env={env}
          onClose={() => setCreating(false)}
          onCreated={(row) => { setCreating(false); reload(); setEditing(row); }}
        />
      )}
      {editing && (
        <DashboardEditor
          env={env}
          dashboard={editing}
          onClose={() => setEditing(null)}
          onSaved={(row) => { setEditing(row); reload(); }}
        />
      )}
    </>
  );
}

function NewDashboardSheet({ env, onClose, onCreated }) {
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [run, busy] = useAction();
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
  const create = async () => {
    const body = { name: slug, title: title.trim() || slug, definition: { pages: [blankPage(1)] } };
    const row = await run(() => post(envPath(env, "/dashboards"), body), "Dashboard created");
    if (row) onCreated(row);
  };
  return (
    <Sheet title="New dashboard" subtitle="Starts with one empty page — add widgets next." icon="layers" tone="lavender" onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !slug} onClick={create}>Create</Button></>}>
      <Field label="Title"><input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Sales overview" /></Field>
      <Field label="URL slug" hint={slug ? `Published at /dashboards/v1/${env}/${slug}` : "a-z, 0-9 and hyphens"}><input className="mono font-mono text-ui-sm" value={name} onChange={(e) => setName(e.target.value)} placeholder="sales-overview" /></Field>
    </Sheet>
  );
}

function DashboardEditor({ env, dashboard, onClose, onSaved }) {
  const [tab, setTab] = useState("pages");
  const [draft, setDraft] = useState(dashboard);
  const [pageId, setPageId] = useState(draft.definition.pages[0]?.id);
  const [editingWidget, setEditingWidget] = useState(null);
  const [run, busy] = useAction();
  const resources = useApi(envPath(env, "/resources"));
  const base = envPath(env, `/dashboards/${dashboard.id}`);

  useEffect(() => { setDraft(dashboard); setPageId(dashboard.definition.pages[0]?.id); }, [dashboard.id]);

  const page = draft.definition.pages.find((p) => p.id === pageId) || draft.definition.pages[0];
  const setPages = (pages) => setDraft({ ...draft, definition: { ...draft.definition, pages } });
  const setPage = (next) => setPages(draft.definition.pages.map((p) => (p.id === next.id ? next : p)));

  const save = async (patchBody = { definition: draft.definition, title: draft.title }) => {
    const row = await run(() => patch(base, patchBody), "Saved");
    if (row) onSaved(row);
  };

  const addPage = () => { const next = blankPage(draft.definition.pages.length + 1); setPages([...draft.definition.pages, next]); setPageId(next.id); };
  const removePage = (id) => { if (draft.definition.pages.length <= 1) return; const pages = draft.definition.pages.filter((p) => p.id !== id); setPages(pages); if (pageId === id) setPageId(pages[0].id); };

  const saveWidget = (widget) => {
    const widgets = page.widgets.some((w) => w.id === widget.id) ? page.widgets.map((w) => (w.id === widget.id ? widget : w)) : [...page.widgets, widget];
    setPage({ ...page, widgets });
    setEditingWidget(null);
  };
  const removeWidget = (id) => setPage({ ...page, widgets: page.widgets.filter((w) => w.id !== id) });

  return (
    <Sheet
      title={draft.title || draft.name}
      subtitle={<code className="faint text-faint">{draft.name}</code>}
      icon="layers"
      tone="lavender"
      tabs={[["pages", "Pages"], ["widgets", "Widgets"], ["publish", "Publish"]]}
      tab={tab}
      onTab={setTab}
      onClose={onClose}
      footer={tab !== "publish" ? <><Button onClick={onClose}>Close</Button><span className="grow flex-1 min-w-0" /><Button variant="primary" disabled={busy} onClick={() => save()}>Save</Button></> : <Button onClick={onClose}>Done</Button>}
    >
      {tab === "pages" && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 12 }}>
          <Field label="Dashboard title"><input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></Field>
          <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Pages</h3><p>Shown as tabs on the published page when there's more than one.</p></div><Button size="sm" onClick={addPage}><Icon name="plus" size={13} />Add page</Button></div>
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 8 }}>
            {draft.definition.pages.map((p) => (
              <div key={p.id} className="card bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-md p-4 [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [&.flush_.card-head_+_*]:[border-top:1px_solid_var(--line)] [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:transform-[none]! [a&:hover]:shadow-none [border:1px_solid_var(--line)] shadow-none [&_>_.spread:first-child]:mb-3! [&_>_.spread_h2]:text-base [&_>_.spread_h2]:font-semibold [&_>_.spread_h2]:tracking-[-0.01em] [&.flush_>_.card-head_+_*]:border-t-0 [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&_>_.spread_.chips]:gap-0 [&_>_.spread_.chips]:flex-nowrap [&_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_>_.spread_.chips]:rounded-sm [&_>_.spread_.chips]:overflow-hidden [&_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_>_.spread_.chip]:p-[0_10px] [&_>_.spread_.chip]:border-0 [&_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_>_.spread_.chip]:rounded-none [&_>_.spread_.chip]:bg-transparent [&_>_.spread_.chip]:text-muted [&_>_.spread_.chip]:text-xs [&_>_.spread_.chip]:font-medium-plus [&_>_.spread_.chip:first-child]:border-l-0 [&_>_.spread_.chip.active]:bg-press [&_>_.spread_.chip.active]:text-text [&_>_.spread_.chip.on]:bg-press [&_>_.spread_.chip.on]:text-text [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100 sunken" style={{ padding: 12, display: "flex", gap: 10, alignItems: "center" }}>
                <Icon name={p.id === pageId ? "check" : "document"} size={14} className={p.id === pageId ? "" : "faint"} />
                <input value={p.name} onChange={(e) => setPage({ ...p, name: e.target.value })} style={{ flex: 1 }} />
                <input className="mono font-mono text-ui-sm" value={p.path} onChange={(e) => setPage({ ...p, path: e.target.value })} style={{ width: 140 }} />
                <Button size="sm" onClick={() => { setPageId(p.id); setTab("widgets"); }}>Widgets ({p.widgets.length})</Button>
                {draft.definition.pages.length > 1 && <Button size="sm" variant="danger" onClick={() => removePage(p.id)}><Icon name="trash" size={13} /></Button>}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "widgets" && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 12 }}>
          <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ justifyContent: "space-between" }}>
            <Field label="Page"><select value={pageId} onChange={(e) => setPageId(e.target.value)}>{draft.definition.pages.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
            <Button size="sm" variant="primary" onClick={() => setEditingWidget(blankWidget())}><Icon name="plus" size={13} />Add widget</Button>
          </div>
          {page.widgets.length === 0 && <p className="hint text-xs font-book text-muted leading-[1.45]">No widgets on this page yet.</p>}
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 8 }}>
            {page.widgets.map((w) => (
              <div key={w.id} className="card bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-md p-4 [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [&.flush_.card-head_+_*]:[border-top:1px_solid_var(--line)] [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:transform-[none]! [a&:hover]:shadow-none [border:1px_solid_var(--line)] shadow-none [&_>_.spread:first-child]:mb-3! [&_>_.spread_h2]:text-base [&_>_.spread_h2]:font-semibold [&_>_.spread_h2]:tracking-[-0.01em] [&.flush_>_.card-head_+_*]:border-t-0 [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&_>_.spread_.chips]:gap-0 [&_>_.spread_.chips]:flex-nowrap [&_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_>_.spread_.chips]:rounded-sm [&_>_.spread_.chips]:overflow-hidden [&_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_>_.spread_.chip]:p-[0_10px] [&_>_.spread_.chip]:border-0 [&_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_>_.spread_.chip]:rounded-none [&_>_.spread_.chip]:bg-transparent [&_>_.spread_.chip]:text-muted [&_>_.spread_.chip]:text-xs [&_>_.spread_.chip]:font-medium-plus [&_>_.spread_.chip:first-child]:border-l-0 [&_>_.spread_.chip.active]:bg-press [&_>_.spread_.chip.active]:text-text [&_>_.spread_.chip.on]:bg-press [&_>_.spread_.chip.on]:text-text [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100 sunken" style={{ padding: 12, display: "flex", gap: 10, alignItems: "center" }}>
                <Icon name={{ metric: "usage", table: "resources", chart: "observability", kanban: "layers", form: "edit" }[w.type] || "layers"} size={15} className="faint text-faint" />
                <div className="grow flex-1 min-w-0"><b>{w.title || WIDGET_TYPES.find(([t]) => t === w.type)?.[1]}</b><div className="hint text-xs font-book text-muted leading-[1.45] mono font-mono text-ui-sm">{w.type} · {w.binding.resource || "no resource"}</div></div>
                <Button size="sm" onClick={() => setEditingWidget(w)}>Edit</Button>
                <Button size="sm" variant="danger" onClick={() => removeWidget(w.id)}><Icon name="trash" size={13} /></Button>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "publish" && <PublishPanel env={env} dashboard={draft} base={base} onChanged={(row) => { setDraft(row); onSaved(row); }} />}

      {editingWidget && (
        <WidgetSheet
          env={env}
          widget={editingWidget}
          resources={resources.data?.data || []}
          onClose={() => setEditingWidget(null)}
          onSave={saveWidget}
        />
      )}
    </Sheet>
  );
}

function PublishPanel({ env, dashboard, base, onChanged }) {
  const [key, setKey] = useState("");
  const [run, busy] = useAction();
  const publicUrl = `${window.location.protocol}//${window.location.host.replace(/^studio\./, "")}/dashboards/v1/${env}/${dashboard.name}`;
  const toggle = async (value) => {
    const body = { public: value };
    if (value && key.trim()) body.publishable_key = key.trim();
    const row = await run(() => put(`${base}/publish`, body), value ? "Published" : "Unpublished");
    if (row) { onChanged(row); setKey(""); }
  };
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 16 }}>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ justifyContent: "space-between" }}>
        <div><b>Public</b><p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: "2px 0 0" }}>Anyone with the link can load the page. Each widget still only shows what its resource's own policy allows.</p></div>
        <Switch checked={dashboard.public} disabled={busy} onChange={toggle} />
      </div>
      {!dashboard.published && (
        <Field label="Publishable key" hint="Paste in this environment's publishable key from API keys — sealed and stored, never shown again. Needed once, the first time you publish.">
          <input className="mono font-mono text-ui-sm" value={key} onChange={(e) => setKey(e.target.value)} placeholder="pk_…" />
        </Field>
      )}
      {dashboard.published && dashboard.public && (
        <div>
          <b className="small text-ui-sm">Published at</b>
          <div style={{ marginTop: 6 }}><CopyText text={publicUrl} label="dashboard URL" /></div>
        </div>
      )}
      {dashboard.published && !dashboard.public && <p className="hint text-xs font-book text-muted leading-[1.45]">A publishable key is already sealed — turning this on republishes without asking for it again.</p>}
    </div>
  );
}

function WidgetSheet({ env, widget, resources, onClose, onSave }) {
  const [draft, setDraft] = useState(widget);
  const b = draft.binding;
  const setB = (patch) => setDraft({ ...draft, binding: { ...b, ...patch } });
  const columns = useApi(b.resource ? envPath(env, `/database/tables/${b.resource}`) : null);
  const fields = columns.data?.columns || [];
  const needsField = b.aggregate && b.aggregate !== "count";
  return (
    <Sheet title={WIDGET_TYPES.find(([t]) => t === draft.type)?.[1] || "Widget"} subtitle={WIDGET_TYPES.find(([t]) => t === draft.type)?.[2]} icon="layers" tone="mint" onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!b.resource} onClick={() => onSave(draft)}>Save widget</Button></>}>
      <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
        <Field label="Type"><select value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value })}>{WIDGET_TYPES.map(([t, label]) => <option key={t} value={t}>{label}</option>)}</select></Field>
        <Field label="Width"><select value={draft.width} onChange={(e) => setDraft({ ...draft, width: e.target.value })}>{WIDTHS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
      </div>
      <Field label="Title" optional><input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="Shown above the widget" /></Field>
      <Field label="Resource"><select value={b.resource} onChange={(e) => setB({ resource: e.target.value })}><option value="">Choose a resource…</option>{resources.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}</select></Field>

      {(draft.type === "metric" || draft.type === "chart") && (
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Aggregate"><select value={b.aggregate || "count"} onChange={(e) => setB({ aggregate: e.target.value })}>{AGGREGATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          {needsField && <Field label="Field"><select value={b.field || ""} onChange={(e) => setB({ field: e.target.value })}><option value="">Choose a field…</option>{fields.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select></Field>}
        </div>
      )}
      {draft.type === "chart" && (
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Group by"><select value={b.group_by || ""} onChange={(e) => setB({ group_by: e.target.value })}><option value="">Choose a field…</option>{fields.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select></Field>
          <Field label="Chart type"><select value={b.chart_type || "bar"} onChange={(e) => setB({ chart_type: e.target.value })}><option value="bar">Bar</option><option value="line">Line</option></select></Field>
        </div>
      )}
      {draft.type === "table" && (
        <>
          <Field label="Columns" hint="None selected shows every column."><div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 6 }}>{fields.map((c) => { const on = (b.fields || []).includes(c.name); return <Button key={c.name} size="sm" variant={on ? "primary" : ""} onClick={() => setB({ fields: on ? (b.fields || []).filter((n) => n !== c.name) : [...(b.fields || []), c.name] })}>{c.name}</Button>; })}</div></Field>
          <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1"><Field label="Sort by" optional><select value={b.sort || ""} onChange={(e) => setB({ sort: e.target.value })}><option value="">Default order</option>{fields.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select></Field><Field label="Row limit"><input type="number" min={1} max={500} value={b.limit || 50} onChange={(e) => setB({ limit: Number(e.target.value) || 50 })} /></Field></div>
        </>
      )}
      {draft.type === "kanban" && (
        <>
          <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
            <Field label="Group by (columns)"><select value={b.group_field || ""} onChange={(e) => setB({ group_field: e.target.value })}><option value="">Choose a field…</option>{fields.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select></Field>
            <Field label="Card title field"><select value={b.card_title_field || ""} onChange={(e) => setB({ card_title_field: e.target.value })}><option value="">Choose a field…</option>{fields.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select></Field>
          </div>
          <Field label="Id field" hint="Usually id."><input className="mono font-mono text-ui-sm" value={b.id_field || "id"} onChange={(e) => setB({ id_field: e.target.value })} /></Field>
        </>
      )}
      {draft.type === "form" && <FormFieldsEditor binding={b} setB={setB} fields={fields} />}

      {(draft.type === "metric" || draft.type === "table" || draft.type === "chart" || draft.type === "kanban") && (
        <FiltersEditor columns={fields} filters={b.filters || []} onChange={(filters) => setB({ filters })} />
      )}
    </Sheet>
  );
}

function FiltersEditor({ columns, filters, onChange }) {
  const add = () => onChange([...filters, { field: columns[0]?.name || "", op: "eq", value: "" }]);
  return (
    <div>
      <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Filters</h3></div><Button size="sm" disabled={!columns.length} onClick={add}><Icon name="plus" size={13} />Add</Button></div>
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 6 }}>
        {filters.map((f, i) => (
          <div key={i} className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}>
            <select value={f.field} onChange={(e) => onChange(filters.map((x, j) => (j === i ? { ...x, field: e.target.value } : x)))}>{columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select>
            <select value={f.op} onChange={(e) => onChange(filters.map((x, j) => (j === i ? { ...x, op: e.target.value } : x)))}>{FILTER_OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            <input className="grow flex-1 min-w-0" value={f.value} onChange={(e) => onChange(filters.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
            <button type="button" className="icon-btn w-control h-control rounded-[calc(var(--r)_-_1px)] grid place-items-center border-0 bg-transparent text-text cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong" aria-label="Remove filter" onClick={() => onChange(filters.filter((_, j) => j !== i))}><Icon name="x" size={14} /></button>
          </div>
        ))}
      </div>
    </div>
  );
}

function FormFieldsEditor({ binding, setB, fields }) {
  const list = binding.fields || [];
  const add = () => setB({ fields: [...list, { name: fields[0]?.name || "", label: "", type: "text" }] });
  return (
    <>
      <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
        <Field label="Mode"><select value={binding.mode || "create"} onChange={(e) => setB({ mode: e.target.value })}><option value="create">Create a record</option><option value="update">Update a record</option></select></Field>
        {binding.mode === "update" && <Field label="Record id from URL param" hint="e.g. ?id=…"><input className="mono font-mono text-ui-sm" value={binding.id_param || "id"} onChange={(e) => setB({ id_param: e.target.value })} /></Field>}
      </div>
      <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Fields</h3></div><Button size="sm" onClick={add}><Icon name="plus" size={13} />Add field</Button></div>
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 6 }}>
        {list.map((f, i) => (
          <div key={i} className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}>
            <select value={f.name} onChange={(e) => setB({ fields: list.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })}>{fields.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select>
            <input className="grow flex-1 min-w-0" placeholder="Label" value={f.label} onChange={(e) => setB({ fields: list.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} />
            <select value={f.type || "text"} onChange={(e) => setB({ fields: list.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)) })}><option value="text">Text</option><option value="number">Number</option></select>
            <button type="button" className="icon-btn w-control h-control rounded-[calc(var(--r)_-_1px)] grid place-items-center border-0 bg-transparent text-text cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong" aria-label="Remove field" onClick={() => setB({ fields: list.filter((_, j) => j !== i) })}><Icon name="x" size={14} /></button>
          </div>
        ))}
      </div>
    </>
  );
}
