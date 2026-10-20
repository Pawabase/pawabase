import { useEffect, useState } from "react";
import { Badge, Button, Card, Field, Loading, Modal, useAction, when } from "./ui";
import { editable } from "../lib/kinds";
import { envPath, get, post, put, useApi } from "../lib/api";

const STATES = {
  in_sync: { label: "In sync", tone: "green" },
  external: { label: "Changed outside", tone: "yellow" },
  pending: { label: "Not applied", tone: "yellow" },
  review: { label: "Needs review", tone: "yellow" },
  no_table: { label: "No table yet", tone: "yellow" },
};

const OPS = {
  create_table: "Create table",
  add_column: "Add column",
  add_index: "Add index",
  retype_column: "Change type",
  drop_column: "Remove column",
  replace_index: "Rebuild index",
  drop_index: "Drop index",
};

/** How a resource's table compares with its definition, as a badge. */
export function SchemaBadge({ report }) {
  if (!report) return <span className="muted text-muted">—</span>;
  const state = STATES[report.state] || STATES.in_sync;
  const count = report.state === "pending" || report.state === "review" ? ` · ${report.pending}` : report.state === "external" ? ` · ${(report.external || []).length}` : "";
  return <Badge tone={state.tone}><span className="dot w-[7px] h-[7px] rounded-full bg-[var(--faint)] flex-none inline-block [&.ok]:bg-ok [&.warn]:bg-warn [&.bad]:bg-danger [&.info]:bg-info" />{state.label}{count}</Badge>;
}

/** The changes waiting to be applied to a resource's table, with the SQL that would run, and what is being kept for rollbacks. */
export function SchemaPanel({ env, name, onChanged, onAdopted }) {
  const path = envPath(env, `/resources/${name}/schema`);
  const state = useApi(path);
  const [run, busy] = useAction();
  const [chosen, setChosen] = useState(null);
  const [typed, setTyped] = useState("");
  const report = state.data;
  useEffect(() => {
    if (report) setChosen(new Set((report.changes || []).filter((c) => c.safe).map((c) => c.id)));
  }, [report]);
  const toggle = (id) => setChosen((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const destructive = report?.changes.filter((c) => !c.safe && chosen?.has(c.id)) || [];
  const needsName = destructive.length > 0;
  const ready = chosen?.size > 0 && (!needsName || typed === name);
  const refresh = () => { state.reload(); onChanged?.(); };
  const apply = async () => {
    const result = await run(
      () => post(`${path}/apply`, { changes: [...chosen], ...(needsName ? { confirm: typed } : {}) }),
      "Applied to the table",
    );
    if (result) { setTyped(""); refresh(); }
  };
  const acknowledge = async () => {
    const result = await run(() => post(`${path}/acknowledge`, {}), "Recorded");
    if (result) refresh();
  };
  const adopt = async () => {
    const result = await run(async () => {
      const row = await get(envPath(env, `/resources/${name}`));
      await put(envPath(env, `/resources/${name}`), { ...editable(row), fields: report.proposal });
      return post(`${path}/acknowledge`, {});
    }, "The definition now follows the table");
    if (result) { refresh(); onAdopted?.(); }
  };
  const purge = async () => {
    const result = await run(() => post(`${path}/purge`, { confirm: typed }), "Removed for good");
    if (result) { setTyped(""); refresh(); }
  };
  return (
    <Loading state={state}>
      {() => report && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
          {(report.external || []).length > 0 && (
            <Card icon="refresh" title="Changed outside Pawabase">
              <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
                <p className="muted text-muted">
                  The table was changed by something other than Pawabase (the SQL console, a migration tool, another app)
                  since {report.since?.at ? when(report.since.at) : "it was last seen"}. Nothing has been changed here.
                </p>
                {(report.external || []).map((change) => (
                  <div key={`${change.kind}:${change.column}`} className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap schema-external">
                    <Badge tone={change.kind === "removed" ? "red" : change.kind === "added" ? "green" : "yellow"}>{change.kind}</Badge>
                    <span>{change.detail}</span>
                    {change.in_definition && change.kind === "added" && <span className="hint text-xs font-book text-muted leading-[1.45]">already in the definition</span>}
                  </div>
                ))}
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
                  <Button variant="primary" disabled={busy} onClick={adopt}>Update the definition to match</Button>
                  <Button disabled={busy} onClick={acknowledge}>Keep the definition</Button>
                </div>
                <span className="hint text-xs font-book text-muted leading-[1.45]">Updating changes only field names and types. Access rules, validation and relations stay as they are.</span>
              </div>
            </Card>
          )}
          <Card
            title="Table and definition"
            actions={<><SchemaBadge report={report} /><Button size="sm" onClick={state.reload}>Refresh</Button></>}
          >
            {(report.changes || []).length === 0 ? (
              <p className="muted text-muted">The table <b className="mono font-mono text-ui-sm">{report.table}</b> matches the definition. Nothing is waiting to be applied.</p>
            ) : (
              <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
                <p className="muted text-muted">
                  Saving a resource changes its definition, not the table. These changes are waiting. Nothing runs until you apply them, and each one can be rolled back.
                </p>
                {(report.changes || []).map((change) => (
                  <label key={change.id} className="schema-change flex gap-3 items-start p-[12px_14px] [border:1px_solid_var(--line)] rounded-sm cursor-pointer [&_input]:mt-[3px] [&_>_.stack]:flex-1 [&_>_.stack]:min-w-0">
                    <input type="checkbox" checked={chosen?.has(change.id) || false} onChange={() => toggle(change.id)} />
                    <span className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
                      <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
                        <b>{OPS[change.op] || change.op}</b>
                        {change.column && <code>{change.column}</code>}
                        {!change.safe && <Badge tone="yellow">{["drop_index", "replace_index"].includes(change.op) ? "Changes an index" : "Rewrites values"}</Badge>}
                      </span>
                      <span className="hint text-xs font-book text-muted leading-[1.45]">{change.detail}</span>
                      <pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg schema-sql text-xs m-0 whitespace-pre-wrap break-words">{change.sql.join(";\n")};</pre>
                    </span>
                  </label>
                ))}
                {needsName && (
                  <Field label={`Type ${name} to confirm`} hint="The old values are kept next to the column until you remove them, so this can be rolled back.">
                    <input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder={name} />
                  </Field>
                )}
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                  <Button variant="primary" disabled={busy || !ready} onClick={apply}>
                    Apply {chosen?.size || 0} change{chosen?.size === 1 ? "" : "s"}
                  </Button>
                </div>
              </div>
            )}
          </Card>
          {(report.retained || []).length > 0 && (
            <Card icon="backups" title="Kept for rollbacks">
              <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
                <p className="muted text-muted">These columns and tables are hidden from your API and held so a rollback can restore them. Removing them frees the space and makes those rollbacks impossible.</p>
                {(report.retained || []).map((item) => (
                  <div key={item.name} className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                    <Badge>{item.kind}</Badge><code>{item.original}</code><span className="hint text-xs font-book text-muted leading-[1.45]">kept as {item.name}</span>
                  </div>
                ))}
                <Field label={`Type ${name} to remove them for good`}>
                  <input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder={name} />
                </Field>
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><Button disabled={busy || typed !== name} onClick={purge}>Remove for good</Button></div>
              </div>
            </Card>
          )}
          {report.exists && (
            <Card icon="resources" title="Columns in the table">
              <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
                {(report.columns || []).map((column) => <Badge key={column.name}>{column.name} · {String(column.type).toLowerCase()}</Badge>)}
              </div>
            </Card>
          )}
        </div>
      )}
    </Loading>
  );
}

/** Every change made to the resource's table, newest first, with a rollback for the latest. */
export function HistoryPanel({ env, name, onChanged }) {
  const path = envPath(env, `/resources/${name}/migrations`);
  const state = useApi(path);
  const [run, busy] = useAction();
  const [confirming, setConfirming] = useState(null);
  const [open, setOpen] = useState(null);
  const rollBack = async () => {
    const id = confirming.id;
    const result = await run(() => post(`${path}/${id}/rollback`), "Rolled back");
    if (result) { setConfirming(null); state.reload(); onChanged?.(); }
  };
  const label = (row) => (row.kind === "external" ? "Outside change" : row.kind === "rollback" ? "Rollback" : row.status === "rolled_back" ? "Rolled back" : row.status === "failed" ? "Failed" : "Applied");
  const tone = (row) => (row.status === "failed" ? "red" : row.kind === "external" || row.kind === "rollback" || row.status === "rolled_back" ? "yellow" : "green");
  return (
    <Loading state={state} empty="No changes yet. Applying the definition to the table starts the history.">
      {(data) => (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
          {data.data.map((row) => (
            <div key={row.id} className="card bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-md p-4 [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [&.flush_.card-head_+_*]:[border-top:1px_solid_var(--line)] [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:transform-[none]! [a&:hover]:shadow-none [border:1px_solid_var(--line)] shadow-none [&_>_.spread:first-child]:mb-3! [&_>_.spread_h2]:text-base [&_>_.spread_h2]:font-semibold [&_>_.spread_h2]:tracking-[-0.01em] [&.flush_>_.card-head_+_*]:border-t-0 [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&_>_.spread_.chips]:gap-0 [&_>_.spread_.chips]:flex-nowrap [&_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_>_.spread_.chips]:rounded-sm [&_>_.spread_.chips]:overflow-hidden [&_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_>_.spread_.chip]:p-[0_10px] [&_>_.spread_.chip]:border-0 [&_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_>_.spread_.chip]:rounded-none [&_>_.spread_.chip]:bg-transparent [&_>_.spread_.chip]:text-muted [&_>_.spread_.chip]:text-xs [&_>_.spread_.chip]:font-medium-plus [&_>_.spread_.chip:first-child]:border-l-0 [&_>_.spread_.chip.active]:bg-press [&_>_.spread_.chip.active]:text-text [&_>_.spread_.chip.on]:bg-press [&_>_.spread_.chip.on]:text-text [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100 schema-entry p-[14px_16px] flex flex-col gap-2">
              <div className="spread flex items-center justify-between gap-3">
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
                  <b>#{row.number}</b>
                  <Badge tone={tone(row)}>{label(row)}</Badge>
                  <span>{row.summary}</span>
                </div>
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                  {row.can_roll_back
                    ? <Button size="sm" disabled={busy} onClick={() => setConfirming(row)}>Roll back</Button>
                    : <span className="hint text-xs font-book text-muted leading-[1.45]">{row.status === "applied" && row.kind === "apply" ? row.cannot_roll_back : ""}</span>}
                  {row.up.length > 0 && <Button size="sm" onClick={() => setOpen(open === row.id ? null : row.id)}>{open === row.id ? "Hide SQL" : "SQL"}</Button>}
                </div>
              </div>
              <div className="hint text-xs font-book text-muted leading-[1.45]">{when(row.created_at)}{row.actor ? ` · ${row.actor}` : ""} · from {row.source}</div>
              {row.kind === "external" && row.changes.length > 0 && (
                <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">{row.changes.map((change) => <span key={`${change.kind}:${change.column}`} className="hint text-xs font-book text-muted leading-[1.45]">{change.detail}</span>)}</div>
              )}
              {row.error && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{row.error}</div>}
              {open === row.id && (
                <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
                  <b>Ran</b><pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg schema-sql text-xs m-0 whitespace-pre-wrap break-words">{row.up.join(";\n")}{row.up.length ? ";" : ""}</pre>
                  {row.down.length > 0 && <><b>To undo</b><pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg schema-sql text-xs m-0 whitespace-pre-wrap break-words">{row.down.join(";\n")};</pre></>}
                </div>
              )}
            </div>
          ))}
          {confirming && (
            <Modal
              title={`Roll back #${confirming.number}?`}
              onClose={() => setConfirming(null)}
              footer={<><Button onClick={() => setConfirming(null)}>Cancel</Button><Button variant="primary" disabled={busy} onClick={rollBack}>Roll back</Button></>}
            >
              <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
                <p>{confirming.summary}</p>
                <p className="muted text-muted">The table goes back to how it was before this change. Values the change retired are restored; anything written to what it added is kept aside, not deleted. The definition is not changed, so the table will show as not applied again.</p>
                <pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg schema-sql text-xs m-0 whitespace-pre-wrap break-words">{confirming.down.join(";\n")};</pre>
              </div>
            </Modal>
          )}
        </div>
      )}
    </Loading>
  );
}

/** Tables that exist in the database but have no resource: pick one to define a resource from it. */
export function ImportTable({ env, base, onClose, onDone }) {
  const state = useApi(envPath(env, "/schema/tables"));
  const [run, busy] = useAction();
  const [names, setNames] = useState({});
  const bring = async (table) => {
    const definition = table.definition;
    const name = (names[table.table] || table.table).trim();
    const result = await run(async () => {
      await post(base, {
        name,
        table: table.table,
        primary_key: definition.primary_key,
        id_type: definition.id_type,
        timestamps: definition.timestamps,
        fields: definition.fields_,
        operations: {},
      });
      return post(envPath(env, `/resources/${name}/schema/acknowledge`), { note: `Imported from the existing table ${table.table}` });
    }, `${name} is now a resource`);
    if (result) onDone();
  };
  return (
    <Modal title="Import a table" onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
        <p className="muted text-muted">Tables in this environment's database that no resource uses. Importing defines a resource from the table's columns. It starts with no API access: you choose who can read and write it.</p>
        <Loading state={state} empty="Every table already has a resource.">
          {(data) => data.data.length === 0 ? <p className="muted text-muted">Every table already has a resource.</p> : data.data.map((table) => (
            <div key={table.table} className="card bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-md p-4 [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [&.flush_.card-head_+_*]:[border-top:1px_solid_var(--line)] [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:transform-[none]! [a&:hover]:shadow-none [border:1px_solid_var(--line)] shadow-none [&_>_.spread:first-child]:mb-3! [&_>_.spread_h2]:text-base [&_>_.spread_h2]:font-semibold [&_>_.spread_h2]:tracking-[-0.01em] [&.flush_>_.card-head_+_*]:border-t-0 [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&_>_.spread_.chips]:gap-0 [&_>_.spread_.chips]:flex-nowrap [&_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_>_.spread_.chips]:rounded-sm [&_>_.spread_.chips]:overflow-hidden [&_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_>_.spread_.chip]:p-[0_10px] [&_>_.spread_.chip]:border-0 [&_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_>_.spread_.chip]:rounded-none [&_>_.spread_.chip]:bg-transparent [&_>_.spread_.chip]:text-muted [&_>_.spread_.chip]:text-xs [&_>_.spread_.chip]:font-medium-plus [&_>_.spread_.chip:first-child]:border-l-0 [&_>_.spread_.chip.active]:bg-press [&_>_.spread_.chip.active]:text-text [&_>_.spread_.chip.on]:bg-press [&_>_.spread_.chip.on]:text-text [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100 schema-entry p-[14px_16px] flex flex-col gap-2">
              <div className="spread flex items-center justify-between gap-3">
                <b className="mono font-mono text-ui-sm">{table.table}</b>
                {table.definition && (
                  <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                    <input value={names[table.table] ?? table.table} onChange={(event) => setNames({ ...names, [table.table]: event.target.value })} style={{ width: 160 }} aria-label="Resource name" />
                    <Button size="sm" variant="primary" disabled={busy} onClick={() => bring(table)}>Import</Button>
                  </div>
                )}
              </div>
              <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
                {table.columns.map((column) => <Badge key={column.name}>{column.name} · {column.field_type}</Badge>)}
              </div>
              {table.problem && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] warn">{table.problem}</div>}
              {table.skipped.length > 0 && <span className="hint text-xs font-book text-muted leading-[1.45]">Left out (not usable as field names): {table.skipped.join(", ")}</span>}
            </div>
          ))}
        </Loading>
      </div>
    </Modal>
  );
}

const ORIGIN = {
  declared: ["Declared", "blue"],
  field: ["From a field", "green"],
  system: ["Key or constraint", null],
  external: ["Made by hand", "yellow"],
};
const STATE = { ok: null, missing: ["Not built yet", "yellow"], stale: ["Will be replaced", "yellow"] };
const OPERATORS = [["=", "is"], ["!=", "is not"], ["<", "<"], ["<=", "≤"], [">", ">"], [">=", "≥"], ["is_null", "is empty"], ["not_null", "is not empty"]];

function bytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function IndexEditor({ index, columns, capabilities, taken, onSave, onClose }) {
  const [draft, setDraft] = useState(index || { name: "", columns: [], unique: false, where: [], method: "btree" });
  const set = (values) => setDraft((d) => ({ ...d, ...values }));
  const asObjects = (draft.columns || []).map((c) => (typeof c === "string" ? { name: c } : c));
  const used = new Set(asObjects.map((c) => c.name));
  const problems = [];
  if (!/^[a-z][a-z0-9_]{0,29}$/.test(draft.name)) problems.push("Name: lowercase letters, digits and underscores, up to 30");
  if (!index && taken.has(draft.name)) problems.push("An index with that name exists");
  if (asObjects.length === 0) problems.push("Choose at least one column");
  const methods = capabilities.methods || ["btree"];
  return (
    <Modal
      title={index ? `Edit ${index.name}` : "Add an index"}
      onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={problems.length > 0} onClick={() => onSave({ ...draft, columns: asObjects })}>Save to the definition</Button></>}
    >
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
        <Field label="Name" hint="Lowercase letters, digits and underscores. Appears in the database as pbx_<table>_<name>_…">
          <input value={draft.name} disabled={!!index} onChange={(e) => set({ name: e.target.value })} placeholder="by_tenant_recent" />
        </Field>
        <Field label="Columns" hint="Order matters: the first column is the one the index narrows by.">
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
            {asObjects.map((c, i) => (
              <div key={c.name} className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                <code style={{ minWidth: 140 }}>{i + 1}. {c.name}</code>
                <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui small text-ui-sm"><input type="checkbox" checked={!!c.desc} disabled={draft.method && draft.method !== "btree"} onChange={(e) => set({ columns: asObjects.map((x) => (x.name === c.name ? { ...x, desc: e.target.checked } : x)) })} /> Newest first</label>
                <Button size="sm" disabled={i === 0} onClick={() => { const next = [...asObjects]; [next[i - 1], next[i]] = [next[i], next[i - 1]]; set({ columns: next }); }}>Up</Button>
                <Button size="sm" onClick={() => set({ columns: asObjects.filter((x) => x.name !== c.name) })}>Remove</Button>
              </div>
            ))}
            <select value="" onChange={(e) => e.target.value && set({ columns: [...asObjects, { name: e.target.value }] })} disabled={asObjects.length >= 8}>
              <option value="">Add a column…</option>
              {columns.filter((c) => !used.has(c)).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </Field>
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 20 }}>
          <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui"><input type="checkbox" checked={!!draft.unique} disabled={draft.method && draft.method !== "btree"} onChange={(e) => set({ unique: e.target.checked })} /> Unique: no two rows may share these values</label>
          {methods.length > 1 && (
            <Field label="Method"><select value={draft.method || "btree"} onChange={(e) => set({ method: e.target.value, unique: e.target.value === "btree" ? draft.unique : false })}>
              {methods.map((m) => <option key={m} value={m}>{{ btree: "B-tree (general)", hash: "Hash (equality only)", brin: "BRIN (huge, ordered tables)" }[m] || m}</option>)}
            </select></Field>
          )}
          {capabilities.concurrent && (
            <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui"><input type="checkbox" checked={!!draft.concurrent} onChange={(e) => set({ concurrent: e.target.checked })} /> Build without blocking writes</label>
          )}
        </div>
        {capabilities.partial ? (
          <Field label="Only index rows where…" optional hint="A partial index is smaller and faster for a common slice, such as rows that are not deleted. Conditions are combined with AND.">
            <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
              {(draft.where || []).map((w, i) => (
                <div key={i} className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                  <select value={w.column} onChange={(e) => set({ where: draft.where.map((x, j) => (j === i ? { ...x, column: e.target.value } : x)) })}>
                    <option value="">column…</option>
                    {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                  <select value={w.op} onChange={(e) => set({ where: draft.where.map((x, j) => (j === i ? { column: x.column, op: e.target.value, ...(["is_null", "not_null"].includes(e.target.value) ? {} : { value: x.value ?? "" }) } : x)) })}>
                    {OPERATORS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                  {!["is_null", "not_null"].includes(w.op) && <input value={w.value ?? ""} onChange={(e) => set({ where: draft.where.map((x, j) => (j === i ? { ...x, value: /^-?\d+(\.\d+)?$/.test(e.target.value) ? Number(e.target.value) : e.target.value } : x)) })} placeholder="value" />}
                  <Button size="sm" onClick={() => set({ where: draft.where.filter((_, j) => j !== i) })}>Remove</Button>
                </div>
              ))}
              <Button size="sm" disabled={(draft.where || []).length >= 4} onClick={() => set({ where: [...(draft.where || []), { column: "", op: "is_null" }] })}>Add a condition</Button>
            </div>
          </Field>
        ) : <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>This database does not support partial indexes.</p>}
        {problems.length > 0 && <ul className="note bad" style={{ margin: 0, paddingLeft: 18 }}>{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
      </div>
    </Modal>
  );
}

/** A table's indexes: those the definition declares, those fields ask for, and those somebody made by hand. */
export function IndexesPanel({ env, name, onChanged }) {
  const path = envPath(env, `/resources/${name}`);
  const state = useApi(`${path}/indexes`);
  const resource = useApi(path);
  const [run, busy] = useAction();
  const [editing, setEditing] = useState(null);
  const [typed, setTyped] = useState("");
  const [dropping, setDropping] = useState(null);
  const data = state.data;
  const body = resource.data;
  const declared = (body?.indexes || []);
  const columns = [...new Set([
    ...(body?.fields || []).map((f) => f.name),
    ...(body?.timestamps !== false ? ["created_at", "updated_at"] : []),
    ...(body?.primary_key ? [body.primary_key] : []),
  ])];
  const refresh = () => { state.reload(); resource.reload(); onChanged?.(); };
  const save = async (next) => {
    const result = await run(() => put(path, { ...editable(body), indexes: next }), "Saved to the definition. Apply it to build the index.");
    if (result) { setEditing(null); refresh(); }
  };
  const apply = async () => {
    const changes = (data.pending || []).map((c) => c.id);
    const risky = (data.pending || []).some((c) => !c.safe);
    const result = await run(() => post(`${path}/schema/apply`, { changes, ...(risky ? { confirm: typed } : {}) }), "Applied to the table");
    if (result) { setTyped(""); refresh(); }
  };
  const drop = async () => {
    const result = await run(() => post(`${path}/indexes/${dropping.physical}/drop`, { confirm: typed, irreversible: dropping.irreversible }), "Index dropped");
    if (result) { setDropping(null); setTyped(""); refresh(); }
  };
  const risky = (data?.pending || []).some((c) => !c.safe);
  return (
    <Loading state={state}>
      {() => data && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
          {(data.pending || []).length > 0 && (
            <Card icon="clock" title="Waiting to be applied">
              <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
                <p className="muted text-muted">Saving an index changes the definition, not the table. Building one reads the whole table, so on a large table it takes a while{data.capabilities?.concurrent ? " (choose “build without blocking writes” to keep writes flowing)" : " and may block writes"}.</p>
                {data.pending.map((c) => (
                  <div key={c.id} className="schema-change flex gap-3 items-start p-[12px_14px] [border:1px_solid_var(--line)] rounded-sm cursor-pointer [&_input]:mt-[3px] [&_>_.stack]:flex-1 [&_>_.stack]:min-w-0">
                    <span className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
                      <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap"><b>{OPS[c.op] || c.op}</b>{c.index && <code>{c.index}</code>}{!c.safe && <Badge tone="yellow">Changes an index</Badge>}</span>
                      <span className="hint text-xs font-book text-muted leading-[1.45]">{c.detail}</span>
                      <pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg schema-sql text-xs m-0 whitespace-pre-wrap break-words">{c.sql.join(";\n")};</pre>
                    </span>
                  </div>
                ))}
                {risky && <Field label={`Type ${name} to confirm`} hint="Dropping or rebuilding an index slows queries until it is back, and removing a unique index lifts the guarantee. The change can be rolled back."><input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={name} /></Field>}
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><Button variant="primary" disabled={busy || (risky && typed !== name) || !data.table_exists} onClick={apply}>Apply {data.pending.length} change{data.pending.length === 1 ? "" : "s"}</Button>{!data.table_exists && <span className="hint text-xs font-book text-muted leading-[1.45]">Create the table first (Columns tab).</span>}</div>
              </div>
            </Card>
          )}
          <Card
            title="Indexes"
            actions={<Button size="sm" variant="primary" disabled={!body} onClick={() => setEditing({})}>Add an index</Button>}
          >
            {(data.data || []).length === 0 ? <p className="muted text-muted">No indexes yet. Add one for the columns your lists filter and sort by.</p> : (
              <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
                {data.data.map((index) => {
                  const origin = ORIGIN[index.origin] || [index.origin, null];
                  const state_ = STATE[index.state];
                  return (
                    <div key={`${index.origin}:${index.physical}`} className="index-row flex items-center justify-between gap-4 p-[12px_0] [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
                      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
                        <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
                          <b className="mono font-mono text-ui-sm">{index.name}</b>
                          <Badge tone={origin[1] || undefined}>{origin[0]}</Badge>
                          {index.unique && <Badge tone="green">unique</Badge>}
                          {index.partial && <Badge>partial</Badge>}
                          {index.method && index.method !== "btree" && <Badge>{index.method}</Badge>}
                          {state_ && <Badge tone={state_[1]}>{state_[0]}</Badge>}
                        </span>
                        <span className="hint text-xs font-book text-muted leading-[1.45]">
                          {index.columns.map((c) => c + ((index.desc || []).includes(c) ? " ↓" : "")).join(", ")}
                          {index.predicate ? ` · where ${index.predicate}` : ""}
                          {index.declared?.where?.length ? ` · where ${index.declared.where.map((w) => `${w.column} ${w.op.replace("_", " ")}${w.value !== undefined ? ` ${JSON.stringify(w.value)}` : ""}`).join(" and ")}` : ""}
                          {index.size != null ? ` · ${bytes(index.size)}` : ""}
                        </span>
                        {index.note && <span className="hint text-xs font-book text-muted leading-[1.45]">{index.note}</span>}
                      </div>
                      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                        {index.origin === "declared" && index.declared && <Button size="sm" onClick={() => setEditing(index.declared)}>Edit</Button>}
                        {index.origin === "declared" && index.declared && <Button size="sm" disabled={busy} onClick={() => save(declared.filter((d) => d.name !== index.name))}>Remove</Button>}
                        {index.droppable && <Button size="sm" onClick={() => { setDropping({ physical: index.physical, name: index.name, partial: index.partial, irreversible: index.partial }); setTyped(""); }}>Drop</Button>}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>
          {editing && (
            <IndexEditor
              index={editing.name ? editing : null}
              columns={columns}
              capabilities={data.capabilities || {}}
              taken={new Set(declared.map((d) => d.name))}
              onClose={() => setEditing(null)}
              onSave={(next) => save(editing.name ? declared.map((d) => (d.name === next.name ? next : d)) : [...declared, next])}
            />
          )}
          {dropping && (
            <Modal title={`Drop ${dropping.name}`} onClose={() => setDropping(null)} footer={<><Button onClick={() => setDropping(null)}>Cancel</Button><Button variant="primary" disabled={busy || typed !== name} onClick={drop}>Drop the index</Button></>}>
              <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
                <p className="muted text-muted">This index was made outside Pawabase. Dropping it is recorded in the History tab{dropping.irreversible ? ", but it is partial or an expression, so a rollback could not rebuild it" : " and can be rolled back"}.</p>
                <Field label={`Type ${name} to confirm`}><input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={name} /></Field>
              </div>
            </Modal>
          )}
        </div>
      )}
    </Loading>
  );
}
