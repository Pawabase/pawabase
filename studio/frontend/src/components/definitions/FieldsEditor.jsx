// Field lists for schemas, resources and route inputs, edited as rows that
// expand into their constraints. Keys the editor does not know about are
// carried through untouched.
import { useState } from "react";
import { Field, IconButton, JsonInput, Popover, Segmented, Switch, TagInput } from "../ui";
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

// Flags worth showing on a collapsed row: the ones that change how the column
// behaves in the API. Unique already implies an index, so it wins over Indexed.
function flagsOf(field) {
  const out = [];
  if (field.nullable) out.push("Nullable");
  if (field.unique) out.push("Unique");
  else if (field.indexed) out.push("Indexed");
  if (field.read_only) out.push("Read only");
  if (field.write_only) out.push("Write only");
  return out;
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
  if (["json", "array", "object", "ref"].includes(t)) {
    return <JsonInput value={has ? field.default : undefined} rows={3} placeholder="No default" onChange={(v) => onChange(v === undefined || v === null ? setKey(field, "default") : { ...field, default: v })} />;
  }
  return (
    <input
      type={isNum(t) ? "number" : "text"}
      value={has ? String(field.default ?? "") : ""}
      placeholder="No default"
      onChange={(e) => onChange(e.target.value === "" ? setKey(field, "default") : { ...field, default: isNum(t) ? Number(e.target.value) : e.target.value })}
    />
  );
}

function TypePicker({ field, onPick }) {
  const type = TYPE[field.type || "string"] || TYPE.string;
  return (
    <Popover
      label="Column type"
      width={286}
      className="field-type-pop flex-none"
      trigger={({ ref, ...rest }) => (
        <button ref={ref} type="button" className="field-type-btn flex items-center gap-1.5 p-[7px_10px] [border:1px_solid_var(--line-2)] rounded-sm bg-surface text-text font-inherit text-ui whitespace-nowrap cursor-pointer [&:hover]:border-brand [&:hover]:bg-brand-soft [&_.icon]:w-[13px] [&_.icon]:h-[13px] [&_.icon]:text-faint" {...rest}>
          <span>{type[1]}</span>
          <Icon name="chevronDown" />
        </button>
      )}
    >
      {({ close }) => FIELD_TYPES.map(([value, label, glyph, tone]) => (
        <button
          key={value}
          type="button"
          role="menuitem"
          className={`type-menu-item flex items-center gap-2.5 w-full p-[7px_9px] border-0 rounded-sm bg-none text-text font-inherit text-left cursor-pointer [&:hover]:bg-sunken [&.on]:bg-sunken [&_>_.icon]:ml-auto [&_>_.icon]:w-3.5 [&_>_.icon]:h-3.5 [&_>_.icon]:text-brand-strong ${(field.type || "string") === value ? "on" : ""}`}
          onClick={() => { onPick(value); close(); }}
        >
          <span className={`field-type-dot w-[22px] h-[22px] rounded-sm grid place-items-center text-3xs font-extrabold font-mono text-pastel-ink pastel text-text pastel-contrast [&.lavender]:bg-lavender [&.peach]:bg-peach [&.mint]:bg-mint [&.butter]:bg-butter [&.sky]:bg-sky [&.rose]:bg-rose [&.lavender]:text-text [&.peach]:text-text [&.mint]:text-text [&.butter]:text-text [&.sky]:text-text [&.rose]:text-text ${tone}`}>{glyph}</span>
          <span className="type-menu-text flex flex-col min-w-0 [&_b]:text-ui [&_b]:font-semibold [&_small]:text-caption [&_small]:text-faint [&_small]:font-mono"><b>{label}</b><small>{value}</small></span>
          {(field.type || "string") === value && <Icon name="check" />}
        </button>
      ))}
    </Popover>
  );
}

function FieldDetails({ field, onChange, schemas, depth, indexable }) {
  const t = field.type || "string";
  return (
    <div className="field-card-body [border-top:1px_solid_var(--line)] p-3.5 flex flex-col gap-3.5 bg-canvas rounded-[0_0_var(--r)_var(--r)]">
      <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
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
            <input className="mono font-mono text-ui-sm" value={field.pattern || ""} placeholder="^[a-z0-9-]+$" onChange={(e) => onChange(setKey(field, "pattern", e.target.value))} />
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
            <div className="list-row flex items-center gap-2 [&_>_input]:flex-1 [&_>_input]:min-w-0 [&_>_select]:flex-1 [&_>_select]:min-w-0">
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
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 20 }}>
        <Switch size="sm" checked={field.nullable} onChange={(v) => onChange(setKey(field, "nullable", v))} label="Nullable" />
        <Switch size="sm" checked={field.read_only} onChange={(v) => onChange(setKey(field, "read_only", v))} label="Read only" />
        {indexable && !["object", "json", "array", "ref"].includes(t) && (
          <>
            <Switch size="sm" checked={field.indexed || field.unique} disabled={!!field.unique} onChange={(v) => onChange(setKey(field, "indexed", v))} label="Indexed" />
            <Switch size="sm" checked={field.unique} onChange={(v) => onChange(setKey(field, "unique", v))} label="Unique" />
          </>
        )}
        <Switch size="sm" checked={field.write_only} onChange={(v) => onChange(setKey(field, "write_only", v))} label="Write only" />
      </div>
      {t === "object" && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm nested">
          <span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Nested fields</span>
          <FieldsEditor value={field.fields || []} onChange={(fields) => onChange({ ...field, fields })} schemas={schemas} depth={depth + 1} />
        </div>
      )}
    </div>
  );
}

export function FieldsEditor({ value = [], onChange, schemas = [], depth = 0, addLabel = "Add field", empty, indexable = false, interactive = false }) {
  const [open, setOpen] = useState(null);
  const [focusAt, setFocusAt] = useState(null);
  const [dragAt, setDragAt] = useState(null);
  const [overAt, setOverAt] = useState(null);
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
    setFocusAt(value.length);
  };
  const reorder = (from, to) => {
    if (from === null || from === to) return;
    const next = [...value];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
    setOpen(null);
  };
  const counts = value.reduce((acc, f) => (f.name ? { ...acc, [f.name]: (acc[f.name] || 0) + 1 } : acc), {});
  const duplicated = (f) => !!f.name && counts[f.name] > 1;
  return (
    <div className="list-rows flex flex-col gap-2">
      {value.length === 0 && empty && <div className="hint text-xs font-book text-muted leading-[1.45]">{empty}</div>}
      {value.map((field, i) => {
        const type = TYPE[field.type || "string"] || TYPE.string;
        const isOpen = open === i;
        const bad = !!field.name && (duplicated(field) || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(field.name));
        const nameInput = (
          <input
            className={`mono font-mono text-ui-sm ${bad ? "invalid" : ""}`}
            value={field.name}
            placeholder="field_name"
            autoFocus={interactive ? i === focusAt : (!field.name && i === value.length - 1)}
            onChange={(e) => update(i, { ...field, name: e.target.value })}
            onKeyDown={interactive ? (e) => {
              if (e.key === "Enter") { e.preventDefault(); add(); }
              else if (e.key === "Escape") setOpen(null);
            } : undefined}
          />
        );
        return (
          <div
            key={i}
            className={`field-card [border:1px_solid_var(--line)] rounded-sm bg-surface [&.open]:border-line-2 [&.open]:shadow-none [&_.nested]:pl-3 [&_.nested]:[border-left:2px_solid_var(--line)] [&.dragging]:opacity-45 [&.drop-here]:border-brand [&.drop-here]:[box-shadow:inset_0_2px_0_var(--brand)] ${isOpen ? "open" : ""} ${dragAt === i ? "dragging" : ""} ${dragAt !== null && overAt === i && dragAt !== i ? "drop-here" : ""}`}
            onDragOver={interactive ? (e) => { e.preventDefault(); if (overAt !== i) setOverAt(i); } : undefined}
            onDrop={interactive ? (e) => { e.preventDefault(); setOverAt(null); setDragAt(null); reorder(dragAt, i); } : undefined}
          >
            {interactive ? (
              <div className="field-card-row grid grid-cols-[22px_minmax(0,_1.3fr)_minmax(0,_1fr)_auto_auto] gap-2 items-center p-[8px_8px_8px_10px] [&_input]:p-[7px_10px] [&_select]:p-[7px_10px] [&_select]:pr-[30px] [&.rich]:flex [&.rich]:flex-wrap [&.rich]:items-center [&.rich]:gap-2 [&.rich]:p-[8px_8px_8px_6px] [&.rich_.field-name-cell]:flex [&.rich_.field-name-cell]:items-center [&.rich_.field-name-cell]:gap-2 [&.rich_.field-name-cell]:[flex:1_1_170px] [&.rich_.field-name-cell]:min-w-0 [&.rich_.field-name-cell_input]:w-full [&.rich_.field-name-cell_input]:p-[7px_10px] max-navigation:grid-cols-[22px_1fr_1fr_auto_auto] rich">
                <button
                  type="button"
                  className="drag-handle flex-none w-[22px] h-[30px] p-0 border-0 rounded-sm bg-none text-faint cursor-grab grid place-items-center [&:hover]:text-text [&:hover]:bg-sunken [&:active]:cursor-grabbing [&_.icon]:w-[15px] [&_.icon]:h-[15px]"
                  aria-label={`Reorder column ${field.name || i + 1}`}
                  title="Drag to reorder, or use the arrow keys"
                  draggable
                  onDragStart={(e) => { setDragAt(i); e.dataTransfer.effectAllowed = "move"; }}
                  onDragEnd={() => { setDragAt(null); setOverAt(null); }}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowUp") { e.preventDefault(); move(i, -1); }
                    else if (e.key === "ArrowDown") { e.preventDefault(); move(i, 1); }
                  }}
                >
                  <Icon name="chevronsUpDown" />
                </button>
                <div className="field-name-cell">
                  <span className={`field-type-dot w-[22px] h-[22px] rounded-sm grid place-items-center text-3xs font-extrabold font-mono text-pastel-ink pastel text-text pastel-contrast [&.lavender]:bg-lavender [&.peach]:bg-peach [&.mint]:bg-mint [&.butter]:bg-butter [&.sky]:bg-sky [&.rose]:bg-rose [&.lavender]:text-text [&.peach]:text-text [&.mint]:text-text [&.butter]:text-text [&.sky]:text-text [&.rose]:text-text ${type[3]}`} title={type[1]}>{type[2]}</span>
                  {nameInput}
                </div>
                <TypePicker field={field} onPick={(v) => update(i, { ...field, type: v })} />
                <div className="field-flags flex flex-wrap items-center gap-[5px] min-w-0">
                  <button
                    type="button"
                    className={`flag-pill p-[2px_7px] [border:1px_solid_var(--line)] rounded-full bg-sunken text-muted text-2xs font-semibold whitespace-nowrap [&.toggle]:border-dashed [&.toggle]:bg-none [&.toggle]:text-faint [&.toggle]:font-inherit [&.toggle]:text-2xs [&.toggle]:font-semibold [&.toggle]:cursor-pointer [&.toggle:hover]:border-line-2 [&.toggle:hover]:text-text [&.toggle.on]:border-solid [&.toggle.on]:border-brand [&.toggle.on]:bg-brand-soft [&.toggle.on]:text-brand-strong toggle ${field.required ? "on" : ""}`}
                    aria-pressed={!!field.required}
                    title="Reject rows that omit this column"
                    onClick={() => update(i, setKey(field, "required", !field.required))}
                  >
                    Required
                  </button>
                  {flagsOf(field).map((flag) => <span key={flag} className="flag-pill p-[2px_7px] [border:1px_solid_var(--line)] rounded-full bg-sunken text-muted text-2xs font-semibold whitespace-nowrap [&.toggle]:border-dashed [&.toggle]:bg-none [&.toggle]:text-faint [&.toggle]:font-inherit [&.toggle]:text-2xs [&.toggle]:font-semibold [&.toggle]:cursor-pointer [&.toggle:hover]:border-line-2 [&.toggle:hover]:text-text [&.toggle.on]:border-solid [&.toggle.on]:border-brand [&.toggle.on]:bg-brand-soft [&.toggle.on]:text-brand-strong">{flag}</span>)}
                </div>
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 0 }}>
                  <IconButton icon={isOpen ? "chevronUp" : "settings"} label={isOpen ? "Collapse" : "More options"} onClick={() => setOpen(isOpen ? null : i)} />
                  <IconButton icon="trash" label="Remove field" onClick={() => { onChange(value.filter((_, j) => j !== i)); setOpen(null); }} />
                </div>
              </div>
            ) : (
              <div className="field-card-row grid grid-cols-[22px_minmax(0,_1.3fr)_minmax(0,_1fr)_auto_auto] gap-2 items-center p-[8px_8px_8px_10px] [&_input]:p-[7px_10px] [&_select]:p-[7px_10px] [&_select]:pr-[30px] [&.rich]:flex [&.rich]:flex-wrap [&.rich]:items-center [&.rich]:gap-2 [&.rich]:p-[8px_8px_8px_6px] [&.rich_.field-name-cell]:flex [&.rich_.field-name-cell]:items-center [&.rich_.field-name-cell]:gap-2 [&.rich_.field-name-cell]:[flex:1_1_170px] [&.rich_.field-name-cell]:min-w-0 [&.rich_.field-name-cell_input]:w-full [&.rich_.field-name-cell_input]:p-[7px_10px] max-navigation:grid-cols-[22px_1fr_1fr_auto_auto]">
                <div className="reorder flex flex-col [&_button]:border-0 [&_button]:bg-none [&_button]:text-faint [&_button]:p-0 [&_button]:h-3 [&_button]:cursor-pointer [&_button]:grid [&_button]:place-items-center [&_button:hover]:text-text [&_button:disabled]:opacity-[0.3] [&_button:disabled]:cursor-default [&_.icon]:w-3.5 [&_.icon]:h-3.5">
                  <button type="button" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><Icon name="chevronUp" /></button>
                  <button type="button" aria-label="Move down" disabled={i === value.length - 1} onClick={() => move(i, 1)}><Icon name="chevronDown" /></button>
                </div>
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 8 }}>
                  <span className={`field-type-dot w-[22px] h-[22px] rounded-sm grid place-items-center text-3xs font-extrabold font-mono text-pastel-ink pastel text-text pastel-contrast [&.lavender]:bg-lavender [&.peach]:bg-peach [&.mint]:bg-mint [&.butter]:bg-butter [&.sky]:bg-sky [&.rose]:bg-rose [&.lavender]:text-text [&.peach]:text-text [&.mint]:text-text [&.butter]:text-text [&.sky]:text-text [&.rose]:text-text ${type[3]}`} title={type[1]}>{type[2]}</span>
                  {nameInput}
                </div>
                <select value={field.type || "string"} onChange={(e) => update(i, { ...field, type: e.target.value })}>
                  {FIELD_TYPES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                </select>
                <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui" style={{ fontSize: 12.5, fontWeight: 600 }}>
                  <input type="checkbox" checked={!!field.required} onChange={(e) => update(i, setKey(field, "required", e.target.checked))} />
                  Required
                </label>
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 0 }}>
                  <IconButton icon={isOpen ? "chevronUp" : "settings"} label={isOpen ? "Collapse" : "More options"} onClick={() => setOpen(isOpen ? null : i)} />
                  <IconButton icon="trash" label="Remove field" onClick={() => { onChange(value.filter((_, j) => j !== i)); setOpen(null); }} />
                </div>
              </div>
            )}
            {interactive && duplicated(field) && <div className="field-warn p-[0_12px_10px_34px] text-danger text-xs">“{field.name}” is used by more than one column.</div>}
            {isOpen && <FieldDetails field={field} schemas={schemas} depth={depth} indexable={indexable} onChange={(f) => update(i, f)} />}
          </div>
        );
      })}
      <button type="button" className="add-row [border:1.5px_dashed_var(--line-2)] bg-none rounded-sm p-2.5 text-muted font-inherit font-semibold text-ui cursor-pointer flex items-center justify-center gap-1.5 [&:hover]:border-brand [&:hover]:text-brand-strong [&:hover]:bg-brand-soft [&_.icon]:w-4 [&_.icon]:h-4" onClick={add}><Icon name="plus" />{addLabel}</button>
    </div>
  );
}
