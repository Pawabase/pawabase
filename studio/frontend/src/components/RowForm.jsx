import { useId } from "react";
import { JsonInput, Segmented, Switch } from "./ui";

/** What kind of input a column wants, from the table's definition when there is one and from the database column type otherwise. */
export function kindOf(column, field) {
  if (field?.enum?.length) return "enum";
  const declared = field?.type;
  if (declared === "boolean") return "boolean";
  if (declared === "integer") return "integer";
  if (declared === "number") return "number";
  if (declared === "date") return "date";
  if (declared === "datetime") return "datetime";
  if (declared === "email") return "email";
  if (declared === "url") return "url";
  if (declared === "text") return "longtext";
  if (["json", "object", "array", "ref"].includes(declared)) return "json";
  if (declared) return "text";
  const db = String(column?.type || "").toLowerCase();
  if (db.includes("bool")) return "boolean";
  if (/\b(int|integer|bigint|smallint|serial)\b/.test(db)) return "integer";
  if (/numeric|decimal|double|real|float|money/.test(db)) return "number";
  if (db === "date") return "date";
  if (db.startsWith("timestamp")) return "datetime";
  if (db.includes("json")) return "json";
  if (db === "text") return "longtext";
  return "text";
}

const KIND_LABEL = {
  enum: "choice", boolean: "yes / no", integer: "whole number", number: "number", date: "date", datetime: "date and time", email: "email",
  url: "link", longtext: "text", json: "JSON", text: "text",
};

/** Values come back from the database as JSON text sometimes; the editor wants the object. */
export function parsed(value) {
  if (typeof value === "string" && /^\s*[[{]/.test(value)) {
    try { return JSON.parse(value); } catch { return value; }
  }
  return value;
}

/** An ISO timestamp as the local value a datetime-local input shows. */
const toLocal = (iso) => {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

function Control({ id, kind, column, field, value, onChange, disabled }) {
  const nullable = column?.nullable !== false && !field?.required;
  if (kind === "boolean") {
    return nullable
      ? <Segmented options={[[true, "Yes"], [false, "No"], [null, "Not set"]]} value={value === undefined ? null : value} onChange={onChange} />
      : <Switch checked={!!value} onChange={onChange} label={value ? "Yes" : "No"} />;
  }
  if (kind === "enum") {
    return (
      <select id={id} value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}>
        {nullable && <option value="">Not set</option>}
        {field.enum.map((option) => <option key={String(option)} value={option}>{String(option)}</option>)}
      </select>
    );
  }
  if (kind === "json") return <JsonInput value={parsed(value) ?? undefined} onChange={(next) => onChange(next)} rows={5} placeholder="Not set" />;
  if (kind === "longtext") {
    return <textarea id={id} rows={4} value={value ?? ""} disabled={disabled} maxLength={field?.max_length || undefined} onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)} />;
  }
  if (kind === "integer" || kind === "number") {
    return (
      <input
        id={id}
        type="number"
        step={kind === "integer" ? 1 : "any"}
        min={field?.minimum}
        max={field?.maximum}
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      />
    );
  }
  if (kind === "date") return <input id={id} type="date" value={value ? String(value).slice(0, 10) : ""} disabled={disabled} onChange={(e) => onChange(e.target.value || null)} />;
  if (kind === "datetime") {
    return (
      <input
        id={id}
        type="datetime-local"
        value={toLocal(value)}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value ? new Date(e.target.value).toISOString() : null)}
      />
    );
  }
  return (
    <input
      id={id}
      type={kind === "email" ? "email" : kind === "url" ? "url" : "text"}
      value={value ?? ""}
      disabled={disabled}
      maxLength={field?.max_length || undefined}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
    />
  );
}

/**
 * One input per column, shaped by its type: switches for yes/no, pickers for dates and numbers, a dropdown where the table lists the
 * allowed values, a JSON editor for structured data and a text area for long text. Short inputs sit two to a row.
 *
 * `columns` are the database columns, `fields` the table's definition by name (optional), `values` what is being edited and `onChange(name, value)`
 * reports each edit. `readOnly` names columns that are shown but not editable.
 */
export default function RowForm({ columns, fields = {}, values, onChange, readOnly = [] }) {
  const prefix = useId();
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-5 max-mobile:grid-cols-1">
      {columns.map((column) => {
        const field = fields[column.name];
        const kind = kindOf(column, field);
        const wide = kind === "json" || kind === "longtext";
        const required = field?.required || column.nullable === false && column.default == null && !column.primary_key;
        const id = `${prefix}-${column.name}`;
        return (
          <div key={column.name} className={`min-w-0 ${wide ? "col-span-2 max-mobile:col-span-1" : ""}`}>
            <label htmlFor={id} className="mb-1.5 flex items-baseline gap-2 text-ui-sm font-medium text-text">
              <span className="font-mono">{column.name}</span>
              {required && <span className="text-danger" title="Required">*</span>}
              <span className="text-2xs font-normal text-faint">{KIND_LABEL[kind]}</span>
            </label>
            <Control id={id} kind={kind} column={column} field={field} value={values[column.name]} disabled={readOnly.includes(column.name) || field?.read_only} onChange={(next) => onChange(column.name, next)} />
            {field?.description && <p className="m-[4px_0_0] text-xs leading-[1.45] text-muted">{field.description}</p>}
          </div>
        );
      })}
    </div>
  );
}
