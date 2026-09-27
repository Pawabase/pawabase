// Policy conditions edited as rules and groups instead of JSON.
//
// A condition is `true`/`false`, a shorthand (`{"role": "admin"}`), a
// comparison (`{"eq": ["$record.owner_id", "$auth.user_id"]}`), a check
// (`{"exists": "$input.email"}`), or a group (`all` / `any` / `not`). The
// builder parses whatever it is given into that tree and writes it back;
// anything it does not recognise is kept as a raw JSON rule, never dropped.
import { Button, IconButton, JsonInput, Segmented } from "../ui";
import { Icon } from "../icons";

const SHORTHANDS = [
  ["authenticated", "Is signed in", null],
  ["role", "Has role", "admin, editor"],
  ["permission", "Has permission", "posts.write"],
  ["owner", "Owns the record (field)", "owner_id"],
  ["service", "Uses a secret key", null],
  ["aal", "Signed in with 2FA", null],
  ["scope", "Key has scope", "resource:write"],
  ["kind", "Caller kind is", "user"],
];
const SHORTHAND_KEYS = SHORTHANDS.map(([k]) => k);

export const OPERATORS = [
  ["eq", "equals"], ["ne", "does not equal"], ["gt", ">"], ["gte", "≥"], ["lt", "<"], ["lte", "≤"],
  ["in", "is one of"], ["not_in", "is not one of"], ["contains", "contains"],
  ["starts_with", "starts with"], ["ends_with", "ends with"], ["matches", "matches regex"],
];
const BINARY = OPERATORS.map(([k]) => k);
const UNARY = [["exists", "exists"], ["truthy", "is set / true"], ["empty", "is empty"]];

const PATHS = [
  "$auth.user_id", "$auth.email", "$auth.roles", "$auth.permissions", "$auth.org_id", "$auth.aal",
  "$record.id", "$record.owner_id", "$record.user_id", "$input.", "$request.ip", "$request.method",
  "$credential.is_service", "$credential.scopes", "$event.name", "$event.data", "$env", "$now",
];

let uid = 0;
const id = () => ++uid;

// ── parse / serialise ─────────────────────────────────────────────────────

function parse(cond) {
  if (cond === true || cond === false) return { id: id(), t: "const", value: cond };
  if (cond && typeof cond === "object" && !Array.isArray(cond) && Object.keys(cond).length === 1) {
    const [[op, arg]] = Object.entries(cond);
    if ((op === "all" || op === "any") && Array.isArray(arg)) return { id: id(), t: "group", mode: op, children: arg.map(parse) };
    if (op === "not") {
      if (arg && typeof arg === "object" && Array.isArray(arg.any)) return { id: id(), t: "group", mode: "none", children: arg.any.map(parse) };
      return { id: id(), t: "group", mode: "none", children: [parse(arg)] };
    }
    if (SHORTHAND_KEYS.includes(op)) return { id: id(), t: "short", op, arg };
    if (BINARY.includes(op) && Array.isArray(arg) && arg.length === 2) return { id: id(), t: "compare", op, left: arg[0], right: arg[1] };
    if (UNARY.some(([k]) => k === op)) return { id: id(), t: "check", op, path: arg };
  }
  return { id: id(), t: "raw", value: cond };
}

export function toTree(cond) {
  const node = parse(cond ?? true);
  if (node.t === "group") return node;
  if (node.t === "const" && node.value === true) return { id: id(), t: "group", mode: "all", children: [] };
  return { id: id(), t: "group", mode: "all", children: [node] };
}

function write(node) {
  switch (node.t) {
    case "const": return node.value;
    case "short": return { [node.op]: node.arg };
    case "compare": return { [node.op]: [node.left, node.right] };
    case "check": return { [node.op]: node.path };
    case "raw": return node.value;
    case "group": {
      const kids = node.children.map(write);
      if (node.mode === "none") return { not: kids.length === 1 ? kids[0] : { any: kids } };
      return { [node.mode]: kids };
    }
    default: return true;
  }
}

/** The condition a tree stands for, with single-child groups flattened. */
export function fromTree(root) {
  if (root.children.length === 0) return root.mode === "any" ? false : true;
  if (root.children.length === 1 && root.mode !== "none") return write(root.children[0]);
  return write(root);
}

// Operands: `$paths` stay strings; numbers, booleans and null become values.
function readOperand(text, list) {
  if (list) return text.split(",").map((t) => readOperand(t.trim())).filter((v) => v !== "");
  const t = text.trim();
  if (t.startsWith("$")) return t;
  if (t === "true") return true;
  if (t === "false") return false;
  if (t === "null") return null;
  if (t !== "" && !Number.isNaN(Number(t))) return Number(t);
  return text;
}

function showOperand(value) {
  if (Array.isArray(value)) return value.map(showOperand).join(", ");
  if (value === null) return "null";
  return value === undefined ? "" : String(value);
}

// ── editor ────────────────────────────────────────────────────────────────

const RULE_TYPES = [
  ...SHORTHANDS.map(([k, label]) => [`short:${k}`, label]),
  ["compare", "Compare values…"],
  ["check", "Check a value…"],
  ["const:false", "Nobody (always deny)"],
];

function blankRule(type) {
  const [t, op] = type.split(":");
  if (t === "short") {
    const defaults = { authenticated: true, service: true, aal: "aal2", owner: "owner_id" };
    return { id: id(), t: "short", op, arg: defaults[op] ?? "" };
  }
  if (t === "compare") return { id: id(), t: "compare", op: "eq", left: "$record.user_id", right: "$auth.user_id" };
  if (t === "check") return { id: id(), t: "check", op: "exists", path: "$input." };
  if (t === "const") return { id: id(), t: "const", value: false };
  return { id: id(), t: "short", op: "authenticated", arg: true };
}

function typeOf(node) {
  if (node.t === "short") return `short:${node.op}`;
  if (node.t === "const") return `const:${node.value}`;
  return node.t;
}

function Rule({ node, onChange, onRemove }) {
  const type = typeOf(node);
  const types = node.t === "raw" ? [["raw", "Custom (JSON)"], ...RULE_TYPES] : node.t === "const" && node.value ? [["const:true", "Anyone (always allow)"], ...RULE_TYPES] : RULE_TYPES;
  return (
    <div className="cond-rule">
      <select value={type} onChange={(e) => onChange(blankRule(e.target.value))} aria-label="Rule type">
        {types.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
      </select>
      {node.t === "short" && ["role", "permission", "scope", "kind", "owner"].includes(node.op) && (
        <input
          value={showOperand(node.arg)}
          placeholder={SHORTHANDS.find(([k]) => k === node.op)[2]}
          onChange={(e) => {
            const text = e.target.value;
            const arg = (node.op === "role" || node.op === "permission") && text.includes(",") ? text.split(",").map((s) => s.trim()) : text;
            onChange({ ...node, arg });
          }}
        />
      )}
      {node.t === "short" && node.op === "authenticated" && (
        <select className="op-select" value={String(node.arg !== false)} onChange={(e) => onChange({ ...node, arg: e.target.value === "true" })}>
          <option value="true">yes</option>
          <option value="false">no (anonymous)</option>
        </select>
      )}
      {node.t === "compare" && (
        <>
          <input list="pb-cond-paths" value={showOperand(node.left)} placeholder="$record.user_id" onChange={(e) => onChange({ ...node, left: readOperand(e.target.value) })} />
          <select className="op-select" value={node.op} onChange={(e) => onChange({ ...node, op: e.target.value })}>
            {OPERATORS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <input
            list="pb-cond-paths"
            value={showOperand(node.right)}
            placeholder={node.op === "in" || node.op === "not_in" ? "a, b, c" : "$auth.user_id or a value"}
            onChange={(e) => onChange({ ...node, right: readOperand(e.target.value, node.op === "in" || node.op === "not_in") })}
          />
        </>
      )}
      {node.t === "check" && (
        <>
          <input list="pb-cond-paths" value={showOperand(node.path)} placeholder="$input.email" onChange={(e) => onChange({ ...node, path: e.target.value })} />
          <select className="op-select" value={node.op} onChange={(e) => onChange({ ...node, op: e.target.value })}>
            {UNARY.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </>
      )}
      {node.t === "raw" && (
        <div style={{ flex: "1 1 100%" }}><JsonInput value={node.value} rows={3} onChange={(value) => onChange({ ...node, value })} /></div>
      )}
      <IconButton icon="x" label="Remove rule" onClick={onRemove} />
    </div>
  );
}

const MODES = [["all", "All of"], ["any", "Any of"], ["none", "None of"]];
const JOIN = { all: "and", any: "or", none: "or" };

function Group({ node, onChange, onRemove, depth = 0, emptyText }) {
  const set = (children) => onChange({ ...node, children });
  return (
    <div className="cond-group">
      <div className="cond-head">
        <Segmented options={MODES} value={node.mode} onChange={(mode) => onChange({ ...node, mode })} />
        <span>these rules pass</span>
        <span className="grow" />
        {onRemove && <IconButton icon="trash" label="Remove group" onClick={onRemove} />}
      </div>
      {node.children.length === 0 && <div className="hint" style={{ padding: "2px 4px" }}>{emptyText || (node.mode === "any" ? "No rules: nobody passes." : "No rules: everyone passes.")}</div>}
      {node.children.map((child, i) => (
        <div key={child.id} className="stack sm">
          {i > 0 && <span className="cond-join">{JOIN[node.mode]}</span>}
          {child.t === "group" ? (
            <Group depth={depth + 1} node={child} onChange={(c) => set(node.children.map((x) => (x.id === child.id ? c : x)))} onRemove={() => set(node.children.filter((x) => x.id !== child.id))} />
          ) : (
            <Rule node={child} onChange={(c) => set(node.children.map((x) => (x.id === child.id ? { ...c, id: child.id } : x)))} onRemove={() => set(node.children.filter((x) => x.id !== child.id))} />
          )}
        </div>
      ))}
      <div className="cond-actions">
        <Button size="sm" variant="soft" onClick={() => set([...node.children, blankRule("short:authenticated")])}><Icon name="plus" />Rule</Button>
        {depth < 2 && <Button size="sm" variant="ghost" onClick={() => set([...node.children, { id: id(), t: "group", mode: "any", children: [] }])}><Icon name="plus" />Group</Button>}
      </div>
    </div>
  );
}

/**
 * Edit a condition. `tree` state lives in the parent (see `toTree`) so rule
 * ids survive re-renders; `onChange(tree, condition)` reports both.
 */
export function ConditionBuilder({ tree, onChange, emptyText }) {
  return (
    <>
      <Group node={tree} onChange={(t) => onChange(t, fromTree(t))} emptyText={emptyText} />
      <datalist id="pb-cond-paths">{PATHS.map((p) => <option key={p} value={p} />)}</datalist>
    </>
  );
}
