// Choose who may do something: a built-in policy, one of the project's
// policies, a parameterised built-in ("role:admin"), or a custom condition.
import { useState } from "react";
import { JsonInput } from "../ui";
import { ConditionBuilder, toTree } from "./ConditionBuilder";

const BUILTINS = [
  ["public", "Anyone (public)"],
  ["authenticated", "Signed-in users"],
  ["owner", "Record owner (owner_id)"],
  ["mfa", "Signed in with 2FA"],
  ["service", "Secret keys & operators"],
  ["deny", "Nobody"],
];
const PARAMS = [["role", "Users with role…", "admin"], ["permission", "Users with permission…", "posts.write"], ["owner", "Owner via field…", "author_id"], ["scope", "Keys with scope…", "resource:write"]];

function modeOf(value) {
  if (value === null || value === undefined) return "__null";
  if (Array.isArray(value)) return "__list";
  if (typeof value === "object" || typeof value === "boolean") return "__custom";
  const [kind, , ] = value.split(":");
  if (value.includes(":") && PARAMS.some(([k]) => k === kind)) return `param:${kind}`;
  return value;
}

export function PolicyPicker({ value, onChange, policies = [], nullLabel = "Default (signed-in users)", allowNull = true }) {
  const mode = modeOf(value);
  const [tree, setTree] = useState(() => toTree(typeof value === "object" && !Array.isArray(value) ? value : { authenticated: true }));
  const known = BUILTINS.map(([k]) => k).concat(policies);
  const choose = (next) => {
    if (next === "__null") onChange(null);
    else if (next === "__custom") { const t = toTree({ authenticated: true }); setTree(t); onChange({ authenticated: true }); }
    else if (next.startsWith("param:")) onChange(`${next.slice(6)}:`);
    else onChange(next);
  };
  return (
    <div className="stack sm">
      <div className="list-row">
        <select value={mode} onChange={(e) => choose(e.target.value)}>
          {allowNull && <option value="__null">{nullLabel}</option>}
          <optgroup label="Built-in">
            {BUILTINS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </optgroup>
          {policies.length > 0 && (
            <optgroup label="This project">
              {policies.map((p) => <option key={p} value={p}>{p}</option>)}
            </optgroup>
          )}
          <optgroup label="More">
            {PARAMS.map(([k, label]) => <option key={k} value={`param:${k}`}>{label}</option>)}
            <option value="__custom">Custom rules…</option>
            {mode === "__list" && <option value="__list">Several policies (JSON)</option>}
          </optgroup>
          {typeof value === "string" && !value.includes(":") && !known.includes(value) && <option value={value}>{value} (unknown)</option>}
        </select>
        {mode.startsWith("param:") && (
          <input
            style={{ maxWidth: 220 }}
            value={value.split(":").slice(1).join(":")}
            placeholder={PARAMS.find(([k]) => `param:${k}` === mode)[2]}
            onChange={(e) => onChange(`${mode.slice(6)}:${e.target.value}`)}
          />
        )}
      </div>
      {mode === "__custom" && <ConditionBuilder tree={tree} onChange={(t, cond) => { setTree(t); onChange(cond); }} />}
      {mode === "__list" && <JsonInput value={value} rows={3} onChange={onChange} />}
    </div>
  );
}
