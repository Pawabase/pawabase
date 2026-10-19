import { useMemo, useState } from "react";
import { Icon } from "./icons";

const ACTIONS = ["read", "create", "update", "delete"];

/** `posts:write` groups under `posts`; a permission with no colon groups under "general". */
const groupOf = (permission) => (permission === "*" ? "everything" : permission.includes(":") ? permission.split(":")[0] : "general");

/**
 * Choose permissions in a searchable table: one row per resource (or prefix), one column per action.
 * Offers what the environment already knows plus `<resource>:read|create|update|delete`, and `*`;
 * anything else is added by name and gets its own cell.
 */
export function PermissionPicker({ value = [], onChange, known = [], resources = [] }) {
  const [query, setQuery] = useState("");
  const [custom, setCustom] = useState("");
  const { rows, columns } = useMemo(() => {
    const all = new Set([...known, ...value]);
    for (const resource of resources) for (const action of ACTIONS) all.add(`${resource}:${action}`);
    all.delete("*");
    const grouped = {};
    const extra = new Set();
    for (const permission of all) {
      const group = groupOf(permission);
      const action = permission.includes(":") ? permission.slice(permission.indexOf(":") + 1) : permission;
      (grouped[group] ||= {})[action] = permission;
      if (!ACTIONS.includes(action)) extra.add(action);
    }
    return { rows: Object.entries(grouped).sort(([x], [y]) => x.localeCompare(y)), columns: [...ACTIONS, ...[...extra].sort()] };
  }, [known, resources, value]);
  const needle = query.trim().toLowerCase();
  const shown = rows.filter(([group, cells]) => !needle || group.toLowerCase().includes(needle) || Object.values(cells).some((p) => p.toLowerCase().includes(needle)));
  const used = columns.filter((column) => shown.some(([, cells]) => cells[column]));
  const toggle = (permission) => onChange(value.includes(permission) ? value.filter((p) => p !== permission) : [...value, permission]);
  const toggleRow = (cells) => {
    const items = Object.values(cells);
    onChange(items.every((p) => value.includes(p)) ? value.filter((p) => !items.includes(p)) : [...new Set([...value, ...items])]);
  };
  const addCustom = () => {
    const names = custom.split(",").map((s) => s.trim()).filter(Boolean);
    if (names.length) onChange([...new Set([...value, ...names])]);
    setCustom("");
  };

  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 10 }}>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 8 }}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search resources or permissions" style={{ flex: 1 }} />
        <span className="muted text-muted" style={{ fontSize: 12.5, whiteSpace: "nowrap" }}>{value.length} selected</span>
        {value.length > 0 && <button type="button" className="chip [border:1px_solid_var(--line)] bg-none text-muted rounded-xs p-[3px_11px] font-inherit text-xs cursor-pointer [&:hover]:border-brand [&:hover]:text-brand-strong [&.on]:border-solid [&.on]:border-brand [&.on]:bg-brand-soft [&.on]:text-brand-strong font-semibold [&.active]:bg-text [&.active]:text-surface [&.active]:border-text" onClick={() => onChange([])}>Clear</button>}
      </div>
      <label className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 8, fontSize: 13 }}>
        <input type="checkbox" checked={value.includes("*")} onChange={() => toggle("*")} />
        <b>*</b> <span className="muted text-muted">everything, including permissions added later</span>
      </label>
      <div style={{ maxHeight: 280, overflow: "auto", border: "1px solid var(--line)", borderRadius: "var(--r)" }}>
        <table className="table" style={{ margin: 0, width: "100%" }}>
          <thead>
            <tr><th style={{ textAlign: "left" }}>Resource</th>{used.map((column) => <th key={column} style={{ textAlign: "center", textTransform: "capitalize" }}>{column}</th>)}<th /></tr>
          </thead>
          <tbody>
            {shown.length === 0 && <tr><td colSpan={used.length + 2} className="muted text-muted">No permission matches. Add it below.</td></tr>}
            {shown.map(([group, cells]) => {
              const items = Object.values(cells);
              const count = items.filter((p) => value.includes(p)).length;
              return (
                <tr key={group}>
                  <td><b>{group}</b></td>
                  {used.map((column) => (
                    <td key={column} style={{ textAlign: "center" }}>
                      {cells[column] ? <input type="checkbox" aria-label={cells[column]} title={cells[column]} checked={value.includes(cells[column])} onChange={() => toggle(cells[column])} /> : <span className="faint text-faint">–</span>}
                    </td>
                  ))}
                  <td style={{ textAlign: "right" }}>{items.length > 1 && <button type="button" className="chip [border:1px_solid_var(--line)] bg-none text-muted rounded-xs p-[3px_11px] font-inherit text-xs cursor-pointer [&:hover]:border-brand [&:hover]:text-brand-strong [&.on]:border-solid [&.on]:border-brand [&.on]:bg-brand-soft [&.on]:text-brand-strong font-semibold [&.active]:bg-text [&.active]:text-surface [&.active]:border-text" onClick={() => toggleRow(cells)}>{count === items.length ? "None" : "All"}</button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 8 }}>
        <input value={custom} onChange={(e) => setCustom(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustom(); } }} placeholder="Another permission, e.g. reports:export" style={{ flex: 1 }} />
        <button type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text sm" disabled={!custom.trim()} onClick={addCustom}>Add</button>
      </div>
    </div>
  );
}

/** Choose roles for a user by clicking them. */
export function RolePicker({ value = [], onChange, roles = [] }) {
  const names = [...new Set([...roles.map((r) => r.name), ...value])];
  const byName = Object.fromEntries(roles.map((r) => [r.name, r]));
  if (names.length === 0) return <span className="muted text-muted">No roles exist yet. Create one on the Roles tab.</span>;
  return (
    <div className="chips flex flex-wrap gap-1.5">
      {names.map((name) => {
        const on = value.includes(name);
        const role = byName[name];
        return (
          <button type="button" key={name} className={`chip [border:1px_solid_var(--line)] bg-none text-muted rounded-xs p-[3px_11px] font-inherit text-xs cursor-pointer [&:hover]:border-brand [&:hover]:text-brand-strong [&.on]:border-solid [&.on]:border-brand [&.on]:bg-brand-soft [&.on]:text-brand-strong font-semibold [&.active]:bg-text [&.active]:text-surface [&.active]:border-text ${on ? "on" : ""}`} aria-pressed={on} title={role ? (role.permissions || []).join(", ") || "No permissions" : "This role no longer exists"}
            onClick={() => onChange(on ? value.filter((r) => r !== name) : [...value, name])}>
            {on && <Icon name="check" size={11} />} {name}{!role && " (missing)"}
          </button>
        );
      })}
    </div>
  );
}
