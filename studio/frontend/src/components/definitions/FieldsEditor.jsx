// Field lists for schemas, resources and route inputs, edited as rows that
// expand into their constraints. Keys the editor does not know about are
// carried through untouched.
import { useState } from "react";
import { Field, IconButton, Segmented, Switch, TagInput } from "../ui";
import { Icon } from "../icons";
import { RefSelect } from "./refs";

export const FIELD_TYPES = [
  ["string", "Text (short)", "Aa", "lavender"],
  ["text", "Text (long)", "¶", "lavender"],
  ["integer", "Integer", "12", "sky"],
  ["number", "Number", "1.5", "sky"],
  ["boolean", "Yes / no", "✓", "mint"],
  ["datetime", "Date & time", "⏱", "butter"],
  ["date", "Date", "D", "butter"],
  ["uuid", "UUID", "id", "peach"],
  ["ulid", "ULID", "id", "peach"],
  ["email", "Email", "@", "rose"],
  ["url", "URL", "↗", "rose"],
  ["json", "JSON", "{}", "peach"],
  ["array", "List", "[ ]", "mint"],
  ["object", "Object", "{ }", "mint"],
  ["ref", "Schema reference", "→", "peach"],
];
const TYPE = Object.fromEntries(FIELD_TYPES.map((t) => [t[0], t]));

const isText = (t) => ["string", "text", "email", "url"].includes(t);
const isNum = (t) => ["integer", "number"].includes(t);

function toNumber(text) {
  return text === "" ? undefined : Number(text);
}

function setKey(field, key, value) {
  const next = { ...field };
  if (value === undefined || value === "" || value === null || (Array.isArray(value) && value.length === 0) || value === false) delete next[key];
  else next[key] = value;
  return next;
}

function DefaultInput({ field, onChange }) {
  const t = field.type || "string";
  const has = "default" in field;
  if (t === "boolean") {
    return (
      <Segmented
        options={[["", "None"], ["true", "Yes"], ["false", "No"]]}
        value={has ? String(field.default) : ""}
        onChange={(v) => onChange(v === "" ? setKey(field, "default") : { ...field, default: v === "true" })}
      />
    );
  }
  if (["json", "array", "object", "ref"].includes(t)) return <span className="hint">Set defaults for this type in JSON view.</span>;
  return (
    <input
      type={isNum(t) ? "number" : "text"}
      value={has ? String(field.default ?? "") : ""}
      placeholder="No default"
      onChange={(e) => onChange(e.target.value === "" ? setKey(field, "default") : { ...field, default: isNum(t) ? Number(e.target.value) : e.target.value })}
    />
  );
}

function FieldDetails({ field, onChange, schemas, depth }) {
  const t = field.type || "string";
  return (
    <div className="field-card-body">
      <div className="form-grid">
        <Field label="Description" optional className="span">
          <input value={field.description || ""} placeholder="Shown in the API docs" onChange={(e) => onChange(setKey(field, "description", e.target.value))} />
        </Field>
        <Field label="Default"><DefaultInput field={field} onChange={onChange} /></Field>
        <Field label="Example" optional>
          <input value={field.example ?? ""} onChange={(e) => onChange(setKey(field, "example", e.target.value))} />
        </Field>
        {isText(t) && (
          <>
            <Field label="Min length" optional><input type="number" min="0" value={field.min_length ?? ""} onChange={(e) => onChange(setKey(field, "min_length", toNumber(e.target.value)))} /></Field>
            <Field label="Max length" optional><input type="number" min="0" value={field.max_length ?? ""} onChange={(e) => onChange(setKey(field, "max_length", toNumber(e.target.value)))} /></Field>
          </>
        )}
        {t === "string" && (
          <Field label="Pattern" optional hint="A regular expression the value must match." className="span">
            <input className="mono" value={field.pattern || ""} placeholder="^[a-z0-9-]+$" onChange={(e) => onChange(setKey(field, "pattern", e.target.value))} />
          </Field>
        )}
        {isNum(t) && (
          <>
            <Field label="Minimum" optional><input type="number" value={field.minimum ?? ""} onChange={(e) => onChange(setKey(field, "minimum", toNumber(e.target.value)))} /></Field>
            <Field label="Maximum" optional><input type="number" value={field.maximum ?? ""} onChange={(e) => onChange(setKey(field, "maximum", toNumber(e.target.value)))} /></Field>
          </>
        )}
        {(t === "string" || isNum(t)) && (
          <Field label="Allowed values" optional hint="Leave empty to allow any value." className="span">
            <TagInput
              value={(field.enum || []).map(String)}
              placeholder="draft, published, archived"
              onChange={(values) => onChange(setKey(field, "enum", isNum(t) ? values.map(Number).filter((n) => !Number.isNaN(n)) : values))}
            />
          </Field>
        )}
        {t === "ref" && (
          <Field label="Schema" className="span">
            <RefSelect options={schemas} value={field.schema} allowEmpty={false} placeholder="Choose a schema" onChange={(v) => onChange(setKey(field, "schema", v))} />
          </Field>
        )}
        {t === "array" && (
          <Field label="Item type" className="span">
            <div className="list-row">
              <select value={field.items?.type || "json"} onChange={(e) => onChange({ ...field, items: { ...(field.items || {}), type: e.target.value } })}>
                {FIELD_TYPES.filter(([v]) => v !== "array").map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </select>
              {field.items?.type === "ref" && (
                <RefSelect options={schemas} value={field.items?.schema} allowEmpty={false} placeholder="Schema" onChange={(v) => onChange({ ...field, items: { ...field.items, schema: v } })} />
              )}
            </div>
          </Field>
        )}
      </div>
      <div className="row wrap" style={{ gap: 20 }}>
        <Switch size="sm" checked={field.nullable} onChange={(v) => onChange(setKey(field, "nullable", v))} label="Nullable" />
        <Switch size="sm" checked={field.read_only} onChange={(v) => onChange(setKey(field, "read_only", v))} label="Read only" />
        <Switch size="sm" checked={field.write_only} onChange={(v) => onChange(setKey(field, "write_only", v))} label="Write only" />
      </div>
      {t === "object" && (
        <div className="stack sm nested">
          <span className="eyebrow">Nested fields</span>
          <FieldsEditor value={field.fields || []} onChange={(fields) => onChange({ ...field, fields })} schemas={schemas} depth={depth + 1} />
        </div>
      )}
    </div>
  );
}

export function FieldsEditor({ value = [], onChange, schemas = [], depth = 0, addLabel = "Add field", empty }) {
  const [open, setOpen] = useState(null);
  const update = (i, field) => onChange(value.map((f, j) => (j === i ? field : f)));
  const move = (i, by) => {
    const next = [...value];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    onChange(next);
    if (open === i) setOpen(i + by);
  };
  const add = () => {
    onChange([...value, { name: "", type: "string" }]);
    setOpen(null);
  };
  return (
    <div className="list-rows">
      {value.length === 0 && empty && <div className="hint">{empty}</div>}
      {value.map((field, i) => {
        const type = TYPE[field.type || "string"] || TYPE.string;
        const isOpen = open === i;
        return (
          <div key={i} className={`field-card ${isOpen ? "open" : ""}`}>
            <div className="field-card-row">
              <div className="reorder">
                <button type="button" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><Icon name="chevronUp" /></button>
                <button type="button" aria-label="Move down" disabled={i === value.length - 1} onClick={() => move(i, 1)}><Icon name="chevronDown" /></button>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <span className={`field-type-dot pastel ${type[3]}`} title={type[1]}>{type[2]}</span>
                <input
                  className={`mono ${field.name && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(field.name) ? "invalid" : ""}`}
                  value={field.name}
                  placeholder="field_name"
                  autoFocus={!field.name && i === value.length - 1}
                  onChange={(e) => update(i, { ...field, name: e.target.value })}
                />
              </div>
              <select value={field.type || "string"} onChange={(e) => update(i, { ...field, type: e.target.value })}>
                {FIELD_TYPES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </select>
              <label className="check" style={{ fontSize: 12.5, fontWeight: 600 }}>
                <input type="checkbox" checked={!!field.required} onChange={(e) => update(i, setKey(field, "required", e.target.checked))} />
                Required
              </label>
              <div className="row" style={{ gap: 0 }}>
                <IconButton icon={isOpen ? "chevronUp" : "settings"} label={isOpen ? "Collapse" : "More options"} onClick={() => setOpen(isOpen ? null : i)} />
                <IconButton icon="trash" label="Remove field" onClick={() => { onChange(value.filter((_, j) => j !== i)); setOpen(null); }} />
              </div>
            </div>
            {isOpen && <FieldDetails field={field} schemas={schemas} depth={depth} onChange={(f) => update(i, f)} />}
          </div>
        );
      })}
      <button type="button" className="add-row" onClick={add}><Icon name="plus" />{addLabel}</button>
    </div>
  );
}
