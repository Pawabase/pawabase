// One form per definition kind. Each receives the body being edited, a
// `patch(changes)` to merge into it, and the project's references. The body
// is exactly what the API stores.
import { useState } from "react";
import { Badge, Choices, Field, JsonInput, KeyValue, Section, Segmented, Switch, TagInput, IconButton } from "../ui";
import { Icon } from "../icons";
import { ConditionBuilder, toTree } from "./ConditionBuilder";
import { FieldsEditor } from "./FieldsEditor";
import { PolicyPicker } from "./PolicyPicker";
import { RefSelect } from "./refs";

const text = (body, key, patch) => ({ value: body[key] ?? "", onChange: (e) => patch({ [key]: e.target.value }) });

function Identity({ body, patch, isNew, nameKey = "name", nameHint, namePlaceholder, children }) {
  return (
    <Section icon="settings" title="Details">
      <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
        <Field label="Name" hint={isNew ? nameHint : "Fixed once created; other definitions refer to it by name."}>
          <input className="mono font-mono text-ui-sm" autoFocus={isNew} placeholder={namePlaceholder} {...text(body, nameKey, patch)} disabled={!isNew} />
        </Field>
        {children}
        <Field label="Description" optional className="span">
          <input placeholder="What is this for?" {...text(body, "description", patch)} />
        </Field>
      </div>
    </Section>
  );
}

function Enabled({ body, patch, label = "Enabled", hint }) {
  return <Switch checked={body.enabled !== false} onChange={(enabled) => patch({ enabled })} label={label} hint={hint} />;
}

function RateLimit({ value, onChange }) {
  // Older resources may hold null here. Treat every non-object value as an
  // unset limit so turning the control on is always safe.
  const limit = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const on = !!limit.limit;
  return (
    <div className="rate-limit-control flex items-center justify-between gap-4 max-mobile:items-start max-mobile:flex-col">
      <Switch checked={on} onChange={(v) => onChange(v ? { limit: 60, window: 60 } : {})} label="Rate limit" hint="Per caller, counted across all gateway instances." />
      <div className="input-group flex items-stretch [&_>_*]:rounded-none [&_>_:first-child]:rounded-[var(--r)_0_0_var(--r)] [&_>_:last-child]:rounded-[0_var(--r)_var(--r)_0] [&_>_:not(:first-child)]:ml-[-1px] [&_>_select]:w-auto [&_>_select]:shrink-0 [&_.addon]:grid [&_.addon]:place-items-center [&_.addon]:p-[0_12px] [&_.addon]:bg-head-bg [&_.addon]:[border:1px_solid_var(--line-2)] [&_.addon]:text-muted [&_.addon]:text-ui-sm [&_.addon]:whitespace-nowrap [&_.addon]:border-line-2 rate-limit-fields [flex:0_0_330px] max-w-[330px] [&_input]:min-w-[52px] [&[aria-disabled=true]]:opacity-[.45] max-mobile:basis-[auto] max-mobile:w-full max-mobile:max-w-none" aria-disabled={!on}>
        <input type="number" min="1" value={on ? limit.limit : 60} disabled={!on} onChange={(e) => onChange({ ...limit, limit: Number(e.target.value) || 1 })} aria-label="Requests" />
        <span className="addon">requests per</span>
        <input type="number" min="1" value={on ? limit.window : 60} disabled={!on} onChange={(e) => onChange({ ...limit, window: Number(e.target.value) || 1 })} aria-label="Seconds" />
        <span className="addon">seconds</span>
      </div>
    </div>
  );
}

function CacheTtl({ value, onChange }) {
  return (
    <Field label="Cache responses" hint="0 turns caching off. Up to a day (86400 seconds).">
      <div className="input-group flex items-stretch [&_>_*]:rounded-none [&_>_:first-child]:rounded-[var(--r)_0_0_var(--r)] [&_>_:last-child]:rounded-[0_var(--r)_var(--r)_0] [&_>_:not(:first-child)]:ml-[-1px] [&_>_select]:w-auto [&_>_select]:shrink-0 [&_.addon]:grid [&_.addon]:place-items-center [&_.addon]:p-[0_12px] [&_.addon]:bg-head-bg [&_.addon]:[border:1px_solid_var(--line-2)] [&_.addon]:text-muted [&_.addon]:text-ui-sm [&_.addon]:whitespace-nowrap [&_.addon]:border-line-2" style={{ maxWidth: 260 }}>
        <input type="number" min="0" max="86400" value={value ?? 0} onChange={(e) => onChange(Number(e.target.value))} />
        <span className="addon">seconds</span>
      </div>
    </Field>
  );
}

// ── schemas ───────────────────────────────────────────────────────────────

function SchemaForm({ body, patch, refs, isNew }) {
  return (
    <>
      <Identity body={body} patch={patch} isNew={isNew} namePlaceholder="Address" nameHint="Starts with a letter; letters, digits and underscores." />
      <Section icon="resources" title="Fields" description="Compiled to a Pydantic model and published in OpenAPI.">
        <FieldsEditor value={body.fields || []} onChange={(fields) => patch({ fields })} schemas={refs.schemas.filter((s) => s !== body.name)} empty="No fields yet." />
      </Section>
    </>
  );
}

// ── transformers ──────────────────────────────────────────────────────────

function TransformerForm({ body, patch, isNew }) {
  const def = body.definition || {};
  const step = (key, value) => {
    const next = { ...def };
    const empty = value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length) || (typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length);
    if (empty) delete next[key];
    else next[key] = value;
    // Keep the stored order the same as the order steps run in.
    patch({ definition: Object.fromEntries(["omit", "pick", "set", "rename", "case"].filter((k) => k in next).map((k) => [k, next[k]])) });
  };
  return (
    <>
      <Identity body={body} patch={patch} isNew={isNew} namePlaceholder="public_user" />
      <Section icon="transformers" title="Steps" description="Applied in this order to every record a consumer receives.">
        <Field label="1 · Omit fields" hint="Removed from the output, e.g. password_hash.">
          <TagInput value={def.omit || []} onChange={(v) => step("omit", v)} placeholder="password_hash" />
        </Field>
        <Field label="2 · Keep only" hint="When set, every other field is dropped.">
          <TagInput value={def.pick || []} onChange={(v) => step("pick", v)} placeholder="id, title, created_at" />
        </Field>
        <Field label="3 · Set values" hint="Values may use {{ record.field }} templates.">
          <KeyValue value={def.set || {}} onChange={(v) => step("set", v)} keyLabel="field" valueLabel="https://example.com/posts/{{ record.id }}" addLabel="Add value" />
        </Field>
        <Field label="4 · Rename">
          <KeyValue value={def.rename || {}} onChange={(v) => step("rename", v)} keyLabel="from" valueLabel="to" addLabel="Add rename" />
        </Field>
        <Field label="5 · Key case">
          <Segmented options={[["", "Unchanged"], ["camel", "camelCase"], ["snake", "snake_case"]]} value={def.case || ""} onChange={(v) => step("case", v)} />
        </Field>
      </Section>
    </>
  );
}

// ── policies ──────────────────────────────────────────────────────────────

function PolicyForm({ body, patch, isNew }) {
  const [tree, setTree] = useState(() => toTree(body.condition));
  return (
    <>
      <Identity body={body} patch={patch} isNew={isNew} namePlaceholder="own_rows" />
      <Section icon="policies" title="Who passes" description="Checks on record fields are pushed down to SQL, so filtered lists stay paginated correctly. Secret keys always pass.">
        <ConditionBuilder tree={tree} onChange={(t, condition) => { setTree(t); patch({ condition }); }} />
      </Section>
    </>
  );
}

// ── resources ─────────────────────────────────────────────────────────────

const OPS = [["list", "GET"], ["get", "GET"], ["create", "POST"], ["update", "PATCH"], ["delete", "DELETE"]];

export function ResourceForm({ body, patch, refs, isNew, mode = "all" }) {
  const ops = body.operations || {};
  const fields = body.fields || [];
  const fieldNames = fields.map((f) => f.name).filter(Boolean);
  const setOp = (op, change) => patch({ operations: { ...ops, [op]: { enabled: false, policy: null, ...ops[op], ...change } } });
  const relations = body.relations || [];
  const setRel = (next) => patch({ relations: next });
  const [openRelation, setOpenRelation] = useState(null);
  return (
    <>
      {mode !== "columns" && <Section icon="settings" title="Details">
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Name" hint={isNew ? `Served at /rest/v1/${body.name || "<name>"}` : "The URL segment; fixed once created."}>
            <input className="mono font-mono text-ui-sm" autoFocus={isNew} placeholder="todos" {...text(body, "name", patch)} disabled={!isNew} />
          </Field>
          <Field label="Table" optional hint="Defaults to the name.">
            <input className="mono font-mono text-ui-sm" placeholder={body.name || "todos"} value={body.table || ""} onChange={(e) => patch({ table: e.target.value || null })} />
          </Field>
          <Field label="Primary key">
            <input className="mono font-mono text-ui-sm" {...text(body, "primary_key", patch)} placeholder="id" />
          </Field>
          <Field label="ID type" hint={isNew ? "Chosen once. ULIDs sort by creation time and do not reveal how many records exist; UUIDs are the choice when something else insists on that format." : "Fixed once records exist: converting a key means creating a new resource and copying the records."}>
            {isNew
              ? <Segmented options={[["ulid", "ULID"], ["uuid", "UUID"]]} value={body.id_type === "uuid" ? "uuid" : "ulid"} onChange={(id_type) => patch({ id_type })} />
              : <input disabled value={{ ulid: "ULID", uuid: "UUID", integer: "Auto-increment (legacy)" }[body.id_type || "integer"]} />}
          </Field>
          <Field label="Description" optional className="span"><input placeholder="What rows live here?" {...text(body, "description", patch)} /></Field>
        </div>
      </Section>}
      {mode !== "settings" && <Section
        icon="resources"
        title="Columns"
        description={`The primary key${body.timestamps !== false ? " and created_at / updated_at are" : " is"} added for you.`}
        actions={fields.length > 0 ? <Badge tone="brand">{fields.length} column{fields.length === 1 ? "" : "s"}</Badge> : undefined}
      >
        <FieldsEditor value={fields} onChange={(next) => patch({ fields: next })} schemas={refs.schemas} indexable interactive empty="No columns yet — add one for each value the API should carry." />
      </Section>}
      {mode !== "columns" && <Section icon="secrets" title="Access" description="Which endpoints exist, and who may call each.">
        <div className="ops-table flex flex-col">
          {OPS.map(([op, method]) => {
            const setting = ops[op] || {};
            return (
              <div key={op} className="op-row grid grid-cols-[200px_1fr] gap-3.5 items-center p-[10px_0] [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0 [&_.op-name]:flex [&_.op-name]:items-center [&_.op-name]:gap-2.5 [&_.op-name]:font-semibold-plus [&_.op-name]:capitalize">
                <div className="op-name">
                  <Switch size="sm" checked={setting.enabled} onChange={(enabled) => setOp(op, { enabled })} />
                  <span className={`op-method font-mono text-[10.5px] font-bold p-[2px_7px] rounded-sm m-${method}`}>{method}</span>
                  {op}
                </div>
                {setting.enabled ? (
                  <PolicyPicker value={setting.policy} onChange={(policy) => setOp(op, { policy })} policies={refs.policies} nullLabel="Secret keys only" />
                ) : (
                  <span className="hint text-xs font-book text-muted leading-[1.45]">Off: the endpoint is not served.</span>
                )}
              </div>
            );
          })}
        </div>
      </Section>}
      {mode !== "settings" && <Section
        icon="link"
        title="Relations"
        description="Include related rows with ?expand=name."
        actions={relations.length > 0 ? <Badge tone="brand">{relations.length} relation{relations.length === 1 ? "" : "s"}</Badge> : undefined}
      >
        {relations.length === 0 && <div className="hint text-xs font-book text-muted leading-[1.45]">No relations yet — add one to join this resource to another.</div>}
        {relations.map((rel, i) => {
          const set = (change) => setRel(relations.map((r, j) => (j === i ? { ...r, ...change } : r)));
          const type = rel.type || "belongs_to";
          const targetFields = refs.fields[rel.resource] || [];
          // A relation with no target yet has nothing to summarise, so it stays open until one is picked.
          const isOpen = openRelation === i || !rel.resource;
          const summary = [rel.name || "Untitled", type === "belongs_to" ? "belongs to" : "has many", [rel.resource, rel.field].filter(Boolean).join(".")].join(" · ");
          return (
            <div key={i} className={`relation-card mb-2 [border:1px_solid_var(--line)] rounded-sm bg-surface [&.open]:border-line-2 ${isOpen ? "open" : ""}`}>
              <div className="relation-card-head flex items-center gap-2 p-[8px_8px_8px_10px]">
                <button type="button" className="relation-card-toggle flex flex-1 items-center gap-2.5 min-w-0 p-0 border-0 bg-none text-text font-inherit text-left cursor-pointer [&_.icon]:w-3.5 [&_.icon]:h-3.5 [&_.icon]:ml-auto [&_.icon]:text-faint" aria-expanded={isOpen} onClick={() => setOpenRelation(isOpen && rel.resource ? null : i)}>
                  <span className={`field-type-dot w-[22px] h-[22px] rounded-sm grid place-items-center text-3xs font-extrabold font-mono text-pastel-ink pastel text-text pastel-contrast [&.lavender]:bg-lavender [&.peach]:bg-peach [&.mint]:bg-mint [&.butter]:bg-butter [&.sky]:bg-sky [&.rose]:bg-rose [&.lavender]:text-text [&.peach]:text-text [&.mint]:text-text [&.butter]:text-text [&.sky]:text-text [&.rose]:text-text ${type === "belongs_to" ? "peach" : "mint"}`}>{type === "belongs_to" ? "→" : "⇄"}</span>
                  <span className="relation-card-summary min-w-0 overflow-hidden text-ui text-ellipsis whitespace-nowrap mono font-mono text-ui-sm">{summary}</span>
                  <Icon name={isOpen ? "chevronUp" : "chevronDown"} />
                </button>
                <IconButton icon="trash" label="Remove relation" onClick={() => { setRel(relations.filter((_, j) => j !== i)); setOpenRelation(null); }} />
              </div>
              {isOpen && (
                <div className="relation-card-body p-3.5 [border-top:1px_solid_var(--line)] rounded-[0_0_var(--r)_var(--r)] bg-canvas">
                  <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
                    <Field label="Name"><input className="mono font-mono text-ui-sm" value={rel.name || ""} placeholder="author" onChange={(e) => set({ name: e.target.value })} /></Field>
                    <Field label="Kind"><Segmented options={[["belongs_to", "Belongs to"], ["has_many", "Has many"]]} value={type} onChange={(t) => set({ type: t })} /></Field>
                    <Field label="Resource"><RefSelect options={refs.resources} value={rel.resource} allowEmpty={false} placeholder="Choose" onChange={(v) => set({ resource: v })} /></Field>
                    <Field label={type === "belongs_to" ? "Foreign key here" : `Foreign key on ${rel.resource || "target"}`}>
                      <RefSelect options={type === "belongs_to" ? fieldNames : targetFields} value={rel.field} allowEmpty={false} placeholder="Choose a field" onChange={(v) => set({ field: v })} />
                    </Field>
                  </div>
                </div>
              )}
            </div>
          );
        })}
        <button type="button" className="add-row [border:1.5px_dashed_var(--line-2)] bg-none rounded-sm p-2.5 text-muted font-inherit font-semibold text-ui cursor-pointer flex items-center justify-center gap-1.5 [&:hover]:border-brand [&:hover]:text-brand-strong [&:hover]:bg-brand-soft [&_.icon]:w-4 [&_.icon]:h-4" onClick={() => { setRel([...relations, { name: "", type: "belongs_to", resource: "", field: "" }]); setOpenRelation(relations.length); }}><Icon name="plus" />Add relation</button>
      </Section>}
      {mode !== "columns" && <Section icon="settings" title="Behaviour">
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Switch checked={body.events !== false} onChange={(events) => patch({ events })} label="Emit events" hint={`${body.name || "name"}.created / updated / deleted`} />
          <Switch checked={!!body.realtime} onChange={(realtime) => patch({ realtime })} label="Broadcast changes" hint="Push row changes to realtime subscribers." />
          <Switch checked={body.timestamps !== false} onChange={(timestamps) => patch({ timestamps })} label="Timestamps" hint="Maintain created_at and updated_at." />
          <Field label="Owner field" optional hint="Filled with the caller's user id on create.">
            <RefSelect options={fieldNames} value={body.owner_field} empty="None" onChange={(owner_field) => patch({ owner_field })} />
          </Field>
          <Field label="Transformer" optional hint="Reshapes rows in responses.">
            <RefSelect options={refs.transformers} value={typeof body.transformer === "string" ? body.transformer : null} onChange={(transformer) => patch({ transformer })} />
          </Field>
          <CacheTtl value={body.cache_ttl} onChange={(cache_ttl) => patch({ cache_ttl })} />
          <div className="span"><RateLimit value={body.rate_limit} onChange={(rate_limit) => patch({ rate_limit })} /></div>
          <Field label="Tags" optional className="span" hint="Groups endpoints in the API docs.">
            <TagInput value={body.tags || []} onChange={(tags) => patch({ tags })} placeholder="billing" />
          </Field>
        </div>
      </Section>}
    </>
  );
}

// ── routes ────────────────────────────────────────────────────────────────

function RouteForm({ body, patch, refs }) {
  const inputMode = body.input_schema ? "schema" : body.input_fields ? "fields" : "none";
  const setInput = (mode) => patch({ input_fields: mode === "fields" ? body.input_fields || [] : null, input_schema: mode === "schema" ? body.input_schema || refs.schemas[0] || "" : null });
  const handlers = body.handler_type === "function" ? refs.functions : refs.flows;
  return (
    <>
      <Section icon="routes" title="Endpoint">
        <Field label="Method and path" hint="Served under /rest/v1. Use {param} for path parameters.">
          <div className="input-group flex items-stretch [&_>_*]:rounded-none [&_>_:first-child]:rounded-[var(--r)_0_0_var(--r)] [&_>_:last-child]:rounded-[0_var(--r)_var(--r)_0] [&_>_:not(:first-child)]:ml-[-1px] [&_>_select]:w-auto [&_>_select]:shrink-0 [&_.addon]:grid [&_.addon]:place-items-center [&_.addon]:p-[0_12px] [&_.addon]:bg-head-bg [&_.addon]:[border:1px_solid_var(--line-2)] [&_.addon]:text-muted [&_.addon]:text-ui-sm [&_.addon]:whitespace-nowrap [&_.addon]:border-line-2">
            <select value={body.method || "POST"} onChange={(e) => patch({ method: e.target.value })} style={{ fontWeight: 700 }}>
              {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}
            </select>
            <span className="addon mono font-mono text-ui-sm">/rest/v1</span>
            <input className="mono font-mono text-ui-sm" autoFocus placeholder="/checkout" {...text(body, "path", patch)} />
          </div>
        </Field>
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Name" optional hint="The operation id in OpenAPI."><input className="mono font-mono text-ui-sm" placeholder="checkout" {...text(body, "name", patch)} /></Field>
          <Field label="Description" optional><input {...text(body, "description", patch)} /></Field>
        </div>
        <Enabled body={body} patch={patch} hint="Disabled routes answer 404." />
      </Section>
      <Section icon="code" title="Handler" description="What runs when the endpoint is called.">
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Type">
            <Segmented options={[["flow", "Flow"], ["function", "Function"]]} value={body.handler_type || "flow"} onChange={(handler_type) => patch({ handler_type, handler: "" })} />
          </Field>
          <Field label={body.handler_type === "function" ? "Function" : "Flow"} hint={handlers.length ? null : `No ${body.handler_type === "function" ? "functions" : "flows"} yet; type a name.`}>
            {handlers.length ? (
              <RefSelect options={handlers} value={body.handler} allowEmpty={false} placeholder="Choose" onChange={(handler) => patch({ handler })} />
            ) : (
              <input className="mono font-mono text-ui-sm" {...text(body, "handler", patch)} />
            )}
          </Field>
        </div>
      </Section>
      <Section icon="secrets" title="Access">
        <PolicyPicker value={body.policy} onChange={(policy) => patch({ policy })} policies={refs.policies} />
      </Section>
      <Section icon="send" title="Request & response">
        <Field label="Request body">
          <Segmented options={[["none", "Anything"], ["fields", "Define fields"], ["schema", "Use a schema"]]} value={inputMode} onChange={setInput} />
        </Field>
        {inputMode === "fields" && <FieldsEditor value={body.input_fields || []} onChange={(input_fields) => patch({ input_fields })} schemas={refs.schemas} />}
        {inputMode === "schema" && <RefSelect options={refs.schemas} value={body.input_schema} allowEmpty={false} placeholder="Choose a schema" onChange={(input_schema) => patch({ input_schema })} />}
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Response schema" optional><RefSelect options={refs.schemas} value={body.response_schema} onChange={(response_schema) => patch({ response_schema })} /></Field>
          <Field label="Transformer" optional><RefSelect options={refs.transformers} value={typeof body.transformer === "string" ? body.transformer : null} onChange={(transformer) => patch({ transformer })} /></Field>
        </div>
      </Section>
      <Section icon="clock" title="Limits">
        <RateLimit value={body.rate_limit} onChange={(rate_limit) => patch({ rate_limit })} />
        <CacheTtl value={body.cache_ttl} onChange={(cache_ttl) => patch({ cache_ttl })} />
        <Field label="Tags" optional><TagInput value={body.tags || []} onChange={(tags) => patch({ tags })} /></Field>
      </Section>
    </>
  );
}

// ── event subscriptions ───────────────────────────────────────────────────

function eventSuggestions(refs) {
  return ["user.created", "user.updated", "user.deleted", "session.created", ...refs.resources.flatMap((r) => [`${r}.created`, `${r}.updated`, `${r}.deleted`, `${r}.*`])];
}

function Target({ body, patch, refs, types }) {
  const t = body.target_type;
  const options = t === "flow" ? refs.flows : t === "function" ? refs.functions : null;
  const labels = { flow: "Flow", function: "Function", realtime: "Realtime channel", event: "Event name" };
  return (
    <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
      <Field label="Run">
        <Segmented options={types.map((v) => [v, labels[v].split(" ")[0]])} value={t} onChange={(target_type) => patch({ target_type, target: "" })} />
      </Field>
      <Field label={labels[t]}>
        {options && options.length ? (
          <RefSelect options={options} value={body.target} allowEmpty={false} placeholder="Choose" onChange={(target) => patch({ target })} />
        ) : (
          <input className="mono font-mono text-ui-sm" placeholder={t === "realtime" ? "orders:{{ event.data.id }}" : t === "event" ? "billing.invoice_paid" : "name"} {...text(body, "target", patch)} />
        )}
      </Field>
    </div>
  );
}

function SubscriptionForm({ body, patch, refs, isNew }) {
  const [tree, setTree] = useState(() => toTree(body.condition ?? true));
  const conditional = body.condition !== null && body.condition !== undefined;
  return (
    <>
      <Identity body={body} patch={patch} isNew={isNew} namePlaceholder="on_signup" />
      <Section icon="schedules" title="When" description="Wildcards match a family: todos.* or user.*">
        <Field label="Event">
          <input className="mono font-mono text-ui-sm" list="pb-events" placeholder="user.created" {...text(body, "event", patch)} />
          <datalist id="pb-events">{eventSuggestions(refs).map((e) => <option key={e} value={e} />)}</datalist>
        </Field>
        <Switch checked={conditional} onChange={(on) => { const t = toTree({ exists: "$event.data" }); setTree(t); patch({ condition: on ? { exists: "$event.data" } : null }); }} label="Only when…" hint="Filter on the event with rules over $event." />
        {conditional && <ConditionBuilder tree={tree} onChange={(t, condition) => { setTree(t); patch({ condition }); }} />}
      </Section>
      <Section icon="bolt" title="Then">
        <Target body={body} patch={patch} refs={refs} types={["flow", "function", "realtime"]} />
        <Enabled body={body} patch={patch} />
      </Section>
    </>
  );
}

// ── webhooks ──────────────────────────────────────────────────────────────

function WebhookForm({ body, patch, refs, isNew }) {
  return (
    <>
      <Identity body={body} patch={patch} isNew={isNew} namePlaceholder="crm" />
      <Section icon="send" title="Delivery">
        <Field label="Endpoint URL"><input className="mono font-mono text-ui-sm" type="url" placeholder="https://example.com/hooks/pawabase" {...text(body, "url", patch)} /></Field>
        <Field label="Events" hint="* sends everything.">
          <TagInput value={body.events || []} onChange={(events) => patch({ events })} suggestions={["*", "user.*", ...refs.resources.map((r) => `${r}.*`)]} />
        </Field>
        <Field label="Extra headers" optional>
          <KeyValue value={body.headers || {}} onChange={(headers) => patch({ headers })} keyLabel="Header" valueLabel="Value" addLabel="Add header" />
        </Field>
      </Section>
      <Section icon="refresh" title="Reliability & signing">
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Attempts" hint="Retries back off exponentially.">
            <input type="number" min="1" max="10" value={body.max_attempts ?? 5} onChange={(e) => patch({ max_attempts: Number(e.target.value) })} />
          </Field>
          <Field label="Signing secret" optional hint={isNew ? "Leave empty to generate one; it is shown once." : "Leave empty to keep the current secret."}>
            <input type="password" autoComplete="new-password" placeholder="At least 16 characters" value={body.secret || ""} onChange={(e) => patch({ secret: e.target.value || undefined })} />
          </Field>
        </div>
        <Enabled body={body} patch={patch} />
      </Section>
    </>
  );
}

// ── inbound hooks ─────────────────────────────────────────────────────────

const VERIFY = [
  ["hmac-sha256", "HMAC-SHA256", "The signature most providers send."],
  ["hmac-sha512", "HMAC-SHA512", "The same, with a longer hash."],
  ["hmac-sha256-ts", "HMAC-SHA256 with timestamp", "Signs the time too, so replayed requests are refused."],
  ["pawabase", "Pawabase signature", "The signed format Pawabase's own webhooks send."],
  ["token", "Shared token", "A secret value sent in a header."],
  ["none", "None", "Accept every request. Not recommended."],
];

function InboundForm({ body, patch, refs, isNew, env }) {
  const verification = body.verification || "hmac-sha256";
  return (
    <>
      <Identity body={body} patch={patch} isNew={isNew} nameKey="slug" namePlaceholder="stripe" nameHint={`Received at /hooks/v1/${env}/${body.slug || "<slug>"}`}>
        <Field label="Display name" optional><input placeholder="Stripe" {...text(body, "name", patch)} /></Field>
      </Identity>
      <Section icon="policies" title="Verification" description="Requests that fail verification are refused before anything runs.">
        <Choices label="Verification" options={VERIFY} value={verification} onChange={(v) => patch({ verification: v })} />
        {verification !== "none" && (
          <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
            {verification !== "pawabase" && (
              <Field label="Signature header"><input className="mono font-mono text-ui-sm" placeholder="x-signature" {...text(body, "signature_header", patch)} /></Field>
            )}
            <Field label="Secret" optional hint={isNew ? "Leave empty to generate one." : "Leave empty to keep the current secret."}>
              <input type="password" autoComplete="new-password" value={body.secret || ""} onChange={(e) => patch({ secret: e.target.value || undefined })} />
            </Field>
          </div>
        )}
        {verification === "none" && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] warn">Anyone who knows the URL can trigger this hook.</div>}
      </Section>
      <Section icon="bolt" title="Then">
        <Target body={body} patch={patch} refs={refs} types={["event", "flow"]} />
        <Enabled body={body} patch={patch} />
      </Section>
    </>
  );
}

// ── schedules ─────────────────────────────────────────────────────────────

const CRON_PRESETS = [
  ["* * * * *", "Every minute"],
  ["*/15 * * * *", "Every 15 minutes"],
  ["0 * * * *", "Hourly"],
  ["0 3 * * *", "Daily at 03:00"],
  ["0 9 * * 1", "Mondays at 09:00"],
  ["0 0 1 * *", "Monthly on the 1st"],
];
const UNITS = [[1, "seconds"], [60, "minutes"], [3600, "hours"], [86400, "days"]];

function ScheduleForm({ body, patch, refs, isNew }) {
  const mode = body.interval_seconds ? "interval" : "cron";
  const seconds = body.interval_seconds || 0;
  const [unit, setUnit] = useState(() => [...UNITS].reverse().find(([u]) => seconds && seconds % u === 0)?.[0] || 60);
  const preset = CRON_PRESETS.find(([c]) => c === body.cron);
  return (
    <>
      <Identity body={body} patch={patch} isNew={isNew} namePlaceholder="nightly_cleanup" />
      <Section icon="schedules" title="When" description={body.cron ? `Cron times are read in ${body.timezone || "UTC"}.` : "Runs on a fixed interval."}>
        <Segmented options={[["cron", "On a schedule"], ["interval", "Every…"]]} value={mode} onChange={(m) => patch(m === "cron" ? { cron: body.cron || "0 3 * * *", interval_seconds: null } : { cron: null, interval_seconds: 3600 })} />
        {mode === "cron" ? (
          <>
            <div className="chips flex flex-wrap gap-1.5">
              {CRON_PRESETS.map(([c, label]) => <button type="button" key={c} className={`chip [border:1px_solid_var(--line)] bg-none text-muted rounded-xs p-[3px_11px] font-inherit text-xs cursor-pointer [&:hover]:border-brand [&:hover]:text-brand-strong [&.on]:border-solid [&.on]:border-brand [&.on]:bg-brand-soft [&.on]:text-brand-strong font-semibold [&.active]:bg-text [&.active]:text-surface [&.active]:border-text ${body.cron === c ? "on" : ""}`} onClick={() => patch({ cron: c })}>{label}</button>)}
            </div>
            <Field label="Cron expression" hint={preset ? preset[1] : "minute hour day-of-month month day-of-week"}>
              <input className="mono font-mono text-ui-sm" value={body.cron || ""} placeholder="0 3 * * *" onChange={(e) => patch({ cron: e.target.value })} />
            </Field>
          </>
        ) : (
          <Field label="Interval" hint="At least 10 seconds.">
            <div className="input-group flex items-stretch [&_>_*]:rounded-none [&_>_:first-child]:rounded-[var(--r)_0_0_var(--r)] [&_>_:last-child]:rounded-[0_var(--r)_var(--r)_0] [&_>_:not(:first-child)]:ml-[-1px] [&_>_select]:w-auto [&_>_select]:shrink-0 [&_.addon]:grid [&_.addon]:place-items-center [&_.addon]:p-[0_12px] [&_.addon]:bg-head-bg [&_.addon]:[border:1px_solid_var(--line-2)] [&_.addon]:text-muted [&_.addon]:text-ui-sm [&_.addon]:whitespace-nowrap [&_.addon]:border-line-2" style={{ maxWidth: 320 }}>
              <input type="number" min="1" value={seconds / unit || ""} onChange={(e) => patch({ interval_seconds: Math.round(Number(e.target.value) * unit) || null })} />
              <select value={unit} onChange={(e) => { const u = Number(e.target.value); setUnit(u); patch({ interval_seconds: Math.round((seconds / unit) * u) }); }}>
                {UNITS.map(([u, label]) => <option key={u} value={u}>{label}</option>)}
              </select>
            </div>
          </Field>
        )}
      </Section>
      <Section icon="bolt" title="Then">
        <Target body={body} patch={patch} refs={refs} types={["flow", "function", "event"]} />
        <Field label="Payload" optional hint="Free-form JSON passed as the input.">
          <JsonInput value={body.payload ?? undefined} rows={4} onChange={(payload) => patch({ payload })} />
        </Field>
        <Enabled body={body} patch={patch} />
      </Section>
      <Section icon="settings" title="Behaviour" description="What happens when a run is still going, or when the scheduler was down.">
        {mode === "cron" && (
          <Field label="Time zone" optional hint="An IANA zone such as Africa/Lagos or Europe/Berlin. Empty is UTC.">
            <input className="mono font-mono text-ui-sm" value={body.timezone || ""} placeholder="UTC" onChange={(e) => patch({ timezone: e.target.value || null })} />
          </Field>
        )}
        <Field label="If the previous run is still going">
          <Segmented options={[["allow", "Run anyway"], ["skip", "Skip this run"]]} value={body.overlap || "allow"} onChange={(overlap) => patch({ overlap })} />
        </Field>
        {mode === "cron" && (
          <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui"><input type="checkbox" checked={Boolean(body.catch_up)} onChange={(e) => patch({ catch_up: e.target.checked })} /> Catch up once on a run missed while the scheduler was down</label>
        )}
      </Section>
    </>
  );
}

// ── buckets ───────────────────────────────────────────────────────────────

const MIME_PRESETS = ["image/*", "video/*", "audio/*", "application/pdf", "text/*"];
const MB = 1024 * 1024;

const FORMATS = ["webp", "avif", "jpeg", "png"];

export function BucketForm({ body, patch, refs, isNew }) {
  const transforms = body.transforms || {};
  const setTransform = (change) => patch({ transforms: { ...transforms, ...change } });
  const number = (name, fallback) => (transforms[name] ?? fallback);
  return (
    <>
      <Section icon="settings" title="Details">
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Name" hint="Lower-case letters, digits and dashes.">
            <input className="mono font-mono text-ui-sm" autoFocus={isNew} disabled={!isNew} placeholder="avatars" {...text(body, "name", patch)} />
          </Field>
          <Field label="Description" optional><input {...text(body, "description", patch)} /></Field>
        </div>
      </Section>
      <Section icon="secrets" title="Access" description="What your application's requests may do. Studio administrators can always manage objects, whatever is set here.">
        <Switch checked={!!body.public} onChange={(v) => patch({ public: v })} label="Public bucket" hint="Anyone with an object's URL may read it. Uploads, listing and deletes still follow the policies below." />
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          {!body.public && (
            <Field label="Who may read">
              <PolicyPicker value={body.read_policy} onChange={(read_policy) => patch({ read_policy })} policies={refs.policies} nullLabel="Default" />
            </Field>
          )}
          <Field label="Who may upload new objects">
            <PolicyPicker value={body.write_policy} onChange={(write_policy) => patch({ write_policy })} policies={refs.policies} nullLabel="Default" />
          </Field>
          <Field label="Who may list objects" hint="Reading an object does not let someone enumerate the bucket.">
            <PolicyPicker value={body.list_policy} onChange={(list_policy) => patch({ list_policy })} policies={refs.policies} nullLabel="Same as read" />
          </Field>
          <Field label="Who may replace objects">
            <PolicyPicker value={body.update_policy} onChange={(update_policy) => patch({ update_policy })} policies={refs.policies} nullLabel="Same as upload" />
          </Field>
          <Field label="Who may delete objects">
            <PolicyPicker value={body.delete_policy} onChange={(delete_policy) => patch({ delete_policy })} policies={refs.policies} nullLabel="Same as upload" />
          </Field>
        </div>
      </Section>
      <Section icon="upload" title="Uploads">
        <Field label="Accepted types" hint="MIME patterns. Empty accepts anything.">
          <TagInput value={body.accepts || []} onChange={(accepts) => patch({ accepts })} suggestions={MIME_PRESETS} placeholder="image/*" />
        </Field>
        <Field label="Largest file" hint="0 for no limit.">
          <div className="input-group flex items-stretch [&_>_*]:rounded-none [&_>_:first-child]:rounded-[var(--r)_0_0_var(--r)] [&_>_:last-child]:rounded-[0_var(--r)_var(--r)_0] [&_>_:not(:first-child)]:ml-[-1px] [&_>_select]:w-auto [&_>_select]:shrink-0 [&_.addon]:grid [&_.addon]:place-items-center [&_.addon]:p-[0_12px] [&_.addon]:bg-head-bg [&_.addon]:[border:1px_solid_var(--line-2)] [&_.addon]:text-muted [&_.addon]:text-ui-sm [&_.addon]:whitespace-nowrap [&_.addon]:border-line-2" style={{ maxWidth: 220 }}>
            <input type="number" min="0" step="0.5" value={body.max_bytes ? +(body.max_bytes / MB).toFixed(2) : 0} onChange={(e) => patch({ max_bytes: Math.round(Number(e.target.value) * MB) })} />
            <span className="addon">MB</span>
          </div>
        </Field>
      </Section>
      <Section icon="send" title="Delivery" description="How objects are served from your application's gateway.">
        <Field label="Browser cache" hint="Seconds a browser may reuse an object before asking again. 0 revalidates every time, which is safest when objects are replaced.">
          <div className="input-group flex items-stretch [&_>_*]:rounded-none [&_>_:first-child]:rounded-[var(--r)_0_0_var(--r)] [&_>_:last-child]:rounded-[0_var(--r)_var(--r)_0] [&_>_:not(:first-child)]:ml-[-1px] [&_>_select]:w-auto [&_>_select]:shrink-0 [&_.addon]:grid [&_.addon]:place-items-center [&_.addon]:p-[0_12px] [&_.addon]:bg-head-bg [&_.addon]:[border:1px_solid_var(--line-2)] [&_.addon]:text-muted [&_.addon]:text-ui-sm [&_.addon]:whitespace-nowrap [&_.addon]:border-line-2" style={{ maxWidth: 220 }}>
            <input type="number" min="0" value={body.cache_max_age ?? 0} onChange={(e) => patch({ cache_max_age: Math.max(0, Number(e.target.value) || 0) })} />
            <span className="addon">seconds</span>
          </div>
        </Field>
      </Section>
      <Section icon="sparkle" title="Image transformations" description="Resize, crop and convert images on request, without changing the stored original. Variants are served only to callers who may read the original.">
        <Switch checked={transforms.enabled !== false} onChange={(enabled) => setTransform({ enabled })} label="Enable transformations" hint="Adds ?width=, ?height=, ?fit=, ?format=, ?quality= and ?rotate= to object URLs." />
        {transforms.enabled !== false && (
          <>
            <Field label="Allowed output formats">
              <TagInput value={transforms.formats || FORMATS} onChange={(formats) => setTransform({ formats: formats.filter((f) => FORMATS.includes(f)) })} suggestions={FORMATS} placeholder="webp" />
            </Field>
            <Switch checked={transforms.auto_format !== false} onChange={(auto_format) => setTransform({ auto_format })} label="Automatic format" hint="?format=auto picks AVIF or WebP when the browser's Accept header allows it." />
            <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
              <Field label="Largest width"><input type="number" min="1" max="8192" value={number("max_width", 4096)} onChange={(e) => setTransform({ max_width: Number(e.target.value) })} /></Field>
              <Field label="Largest height"><input type="number" min="1" max="8192" value={number("max_height", 4096)} onChange={(e) => setTransform({ max_height: Number(e.target.value) })} /></Field>
              <Field label="Default quality" hint="1 to 100."><input type="number" min="1" max="100" value={number("quality", 80)} onChange={(e) => setTransform({ quality: Number(e.target.value) })} /></Field>
              <Field label="Largest output" hint="MB."><input type="number" min="0.1" step="0.5" value={+(number("max_output_bytes", 10 * MB) / MB).toFixed(2)} onChange={(e) => setTransform({ max_output_bytes: Math.round(Number(e.target.value) * MB) })} /></Field>
              <Field label="Largest source image" hint="MB. Larger originals are not transformed."><input type="number" min="0.1" step="1" value={+(number("max_source_bytes", 25 * MB) / MB).toFixed(2)} onChange={(e) => setTransform({ max_source_bytes: Math.round(Number(e.target.value) * MB) })} /></Field>
              <Field label="Simultaneous transformations" hint="Per bucket, per server process."><input type="number" min="1" max="16" value={number("max_concurrent", 4)} onChange={(e) => setTransform({ max_concurrent: Number(e.target.value) })} /></Field>
              <Field label="Time limit" hint="Seconds per transformation."><input type="number" min="1" max="30" value={number("timeout_seconds", 10)} onChange={(e) => setTransform({ timeout_seconds: Number(e.target.value) })} /></Field>
            </div>
          </>
        )}
      </Section>
    </>
  );
}

export const FORMS = {
  schemas: SchemaForm,
  transformers: TransformerForm,
  policies: PolicyForm,
  resources: ResourceForm,
  routes: RouteForm,
  subscriptions: SubscriptionForm,
  webhooks: WebhookForm,
  "inbound-hooks": InboundForm,
  schedules: ScheduleForm,
  buckets: BucketForm,
};

/** A reason the body cannot be saved yet, or null. */
export function problem(kind, body) {
  const key = kind === "inbound-hooks" ? "slug" : kind === "routes" ? "path" : "name";
  if (!body[key]) return `Add a ${key}.`;
  if (kind === "routes" && !body.handler) return "Choose a handler.";
  if ((kind === "subscriptions" || kind === "inbound-hooks" || kind === "schedules") && !body.target && body.target_type !== "realtime") return "Choose what to run.";
  if (kind === "subscriptions" && !body.event) return "Choose an event.";
  if (kind === "webhooks" && !/^https?:\/\//.test(body.url || "")) return "Add an http(s) URL.";
  if (kind === "schedules" && !body.cron && !body.interval_seconds) return "Set when it runs.";
  const unnamed = (fields = []) => fields.some((f) => !f.name || (f.type === "object" && unnamed(f.fields)));
  if (unnamed(body.fields) || unnamed(body.input_fields || [])) return "Every field needs a name.";
  return null;
}
