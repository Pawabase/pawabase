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
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 8 }}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search resources or permissions" style={{ flex: 1 }} />
        <span className="muted" style={{ fontSize: 12.5, whiteSpace: "nowrap" }}>{value.length} selected</span>
        {value.length > 0 && <button type="button" className="chip" onClick={() => onChange([])}>Clear</button>}
      </div>
      <label className="row" style={{ gap: 8, fontSize: 13 }}>
        <input type="checkbox" checked={value.includes("*")} onChange={() => toggle("*")} />
        <b>*</b> <span className="muted">everything, including permissions added later</span>
      </label>
      <div style={{ maxHeight: 280, overflow: "auto", border: "1px solid var(--line)", borderRadius: 12 }}>
        <table className="table" style={{ margin: 0, width: "100%" }}>
          <thead>
            <tr><th style={{ textAlign: "left" }}>Resource</th>{used.map((column) => <th key={column} style={{ textAlign: "center", textTransform: "capitalize" }}>{column}</th>)}<th /></tr>
          </thead>
          <tbody>
            {shown.length === 0 && <tr><td colSpan={used.length + 2} className="muted">No permission matches. Add it below.</td></tr>}
            {shown.map(([group, cells]) => {
              const items = Object.values(cells);
              const count = items.filter((p) => value.includes(p)).length;
              return (
                <tr key={group}>
                  <td><b>{group}</b></td>
                  {used.map((column) => (
                    <td key={column} style={{ textAlign: "center" }}>
                      {cells[column] ? <input type="checkbox" aria-label={cells[column]} title={cells[column]} checked={value.includes(cells[column])} onChange={() => toggle(cells[column])} /> : <span className="faint">–</span>}
                    </td>
                  ))}
                  <td style={{ textAlign: "right" }}>{items.length > 1 && <button type="button" className="chip" onClick={() => toggleRow(cells)}>{count === items.length ? "None" : "All"}</button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ gap: 8 }}>
        <input value={custom} onChange={(e) => setCustom(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustom(); } }} placeholder="Another permission, e.g. reports:export" style={{ flex: 1 }} />
        <button type="button" className="btn sm" disabled={!custom.trim()} onClick={addCustom}>Add</button>
      </div>
    </div>
  );
}

/** Choose roles for a user by clicking them. */
export function RolePicker({ value = [], onChange, roles = [] }) {
  const names = [...new Set([...roles.map((r) => r.name), ...value])];
  const byName = Object.fromEntries(roles.map((r) => [r.name, r]));
  if (names.length === 0) return <span className="muted">No roles exist yet. Create one on the Roles tab.</span>;
  return (
    <div className="chips">
      {names.map((name) => {
        const on = value.includes(name);
        const role = byName[name];
        return (
          <button type="button" key={name} className={`chip ${on ? "on" : ""}`} aria-pressed={on} title={role ? (role.permissions || []).join(", ") || "No permissions" : "This role no longer exists"}
            onClick={() => onChange(on ? value.filter((r) => r !== name) : [...value, name])}>
            {on && <Icon name="check" size={11} />} {name}{!role && " (missing)"}
          </button>
        );
      })}
    </div>
  );
}
