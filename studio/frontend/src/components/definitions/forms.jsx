// One form per definition kind. Each receives the body being edited, a
// `patch(changes)` to merge into it, and the project's references. The body
// is exactly what the API stores, so the JSON tab and the form always agree.
import { useState } from "react";
import { Field, JsonInput, KeyValue, Section, Segmented, Switch, TagInput, IconButton } from "../ui";
import { Icon } from "../icons";
import { ConditionBuilder, toTree } from "./ConditionBuilder";
import { FieldsEditor } from "./FieldsEditor";
import { PolicyPicker } from "./PolicyPicker";
import { RefSelect } from "./refs";

const text = (body, key, patch) => ({ value: body[key] ?? "", onChange: (e) => patch({ [key]: e.target.value }) });

function Identity({ body, patch, isNew, nameKey = "name", nameHint, namePlaceholder, children }) {
  return (
    <Section title="Details">
      <div className="form-grid">
        <Field label="Name" hint={isNew ? nameHint : "Fixed once created; other definitions refer to it by name."}>
          <input className="mono" autoFocus={isNew} placeholder={namePlaceholder} {...text(body, nameKey, patch)} disabled={!isNew} />
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

function RateLimit({ value = {}, onChange }) {
  const on = !!value.limit;
  return (
    <div className="stack sm">
      <Switch checked={on} onChange={(v) => onChange(v ? { limit: 60, window: 60 } : {})} label="Rate limit" hint="Per caller, counted across all gateway instances." />
      {on && (
        <div className="input-group" style={{ maxWidth: 420 }}>
          <input type="number" min="1" value={value.limit} onChange={(e) => onChange({ ...value, limit: Number(e.target.value) })} />
          <span className="addon">requests per</span>
          <input type="number" min="1" value={value.window} onChange={(e) => onChange({ ...value, window: Number(e.target.value) })} />
          <span className="addon">seconds</span>
        </div>
      )}
    </div>
  );
}

function CacheTtl({ value, onChange }) {
  return (
    <Field label="Cache responses" hint="0 turns caching off. Up to a day (86400 seconds).">
      <div className="input-group" style={{ maxWidth: 260 }}>
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
      <Section title="Fields" description="Compiled to a Pydantic model and published in OpenAPI.">
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
      <Section title="Steps" description="Applied in this order to every record a consumer receives.">
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
      <Section title="Who passes" description="Checks on record fields are pushed down to SQL, so filtered lists stay paginated correctly. Secret keys always pass.">
        <ConditionBuilder tree={tree} onChange={(t, condition) => { setTree(t); patch({ condition }); }} />
      </Section>
    </>
  );
}

// ── resources ─────────────────────────────────────────────────────────────

const OPS = [["list", "GET"], ["get", "GET"], ["create", "POST"], ["update", "PATCH"], ["delete", "DELETE"]];

function ResourceForm({ body, patch, refs, isNew }) {
  const ops = body.operations || {};
  const fieldNames = (body.fields || []).map((f) => f.name).filter(Boolean);
  const setOp = (op, change) => patch({ operations: { ...ops, [op]: { enabled: false, policy: null, ...ops[op], ...change } } });
  const relations = body.relations || [];
  const setRel = (next) => patch({ relations: next });
  return (
    <>
      <Section title="Details">
        <div className="form-grid">
          <Field label="Name" hint={isNew ? `Served at /rest/v1/${body.name || "<name>"}` : "The URL segment; fixed once created."}>
            <input className="mono" autoFocus={isNew} placeholder="todos" {...text(body, "name", patch)} disabled={!isNew} />
          </Field>
          <Field label="Table" optional hint="Defaults to the name.">
            <input className="mono" placeholder={body.name || "todos"} value={body.table || ""} onChange={(e) => patch({ table: e.target.value || null })} />
          </Field>
          <Field label="Primary key">
            <input className="mono" {...text(body, "primary_key", patch)} placeholder="id" />
          </Field>
          <Field label="ID type" hint="Chosen once: changing it later does not convert existing rows. ULIDs are sortable by creation time and do not reveal how many records exist.">
            <Segmented options={[["ulid", "ULID"], ["uuid", "UUID"], ["integer", "Auto-increment"]]} value={body.id_type || "integer"} onChange={(id_type) => patch({ id_type })} />
          </Field>
          <Field label="Description" optional className="span"><input placeholder="What rows live here?" {...text(body, "description", patch)} /></Field>
        </div>
      </Section>
      <Section title="Fields" description={`The primary key${body.timestamps !== false ? " and created_at / updated_at are" : " is"} added for you.`}>
        <FieldsEditor value={body.fields || []} onChange={(fields) => patch({ fields })} schemas={refs.schemas} />
      </Section>
      <Section title="Access" description="Which endpoints exist, and who may call each.">
        <div className="ops-table">
          {OPS.map(([op, method]) => {
            const setting = ops[op] || {};
            return (
              <div key={op} className="op-row">
                <div className="op-name">
                  <Switch size="sm" checked={setting.enabled} onChange={(enabled) => setOp(op, { enabled })} />
                  <span className={`op-method m-${method}`}>{method}</span>
                  {op}
                </div>
                {setting.enabled ? (
                  <PolicyPicker value={setting.policy} onChange={(policy) => setOp(op, { policy })} policies={refs.policies} nullLabel="Secret keys only" />
                ) : (
                  <span className="hint">Off: the endpoint is not served.</span>
                )}
              </div>
            );
          })}
        </div>
      </Section>
      <Section
        title="Relations"
        description="Include related rows with ?expand=name."
      >
        {relations.map((rel, i) => {
          const set = (change) => setRel(relations.map((r, j) => (j === i ? { ...r, ...change } : r)));
          const type = rel.type || "belongs_to";
          const targetFields = refs.fields[rel.resource] || [];
          return (
            <div key={i} className="card sunken" style={{ padding: 14 }}>
              <div className="form-grid">
                <Field label="Name"><input className="mono" value={rel.name || ""} placeholder="author" onChange={(e) => set({ name: e.target.value })} /></Field>
                <Field label="Kind"><Segmented options={[["belongs_to", "Belongs to"], ["has_many", "Has many"]]} value={type} onChange={(t) => set({ type: t })} /></Field>
                <Field label="Resource"><RefSelect options={refs.resources} value={rel.resource} allowEmpty={false} placeholder="Choose" onChange={(v) => set({ resource: v })} /></Field>
                <Field label={type === "belongs_to" ? "Foreign key here" : `Foreign key on ${rel.resource || "target"}`}>
                  <RefSelect options={type === "belongs_to" ? fieldNames : targetFields} value={rel.field} allowEmpty={false} placeholder="Choose a field" onChange={(v) => set({ field: v })} />
                </Field>
              </div>
              <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
                <IconButton icon="trash" label="Remove relation" onClick={() => setRel(relations.filter((_, j) => j !== i))} />
              </div>
            </div>
          );
        })}
        <button type="button" className="add-row" onClick={() => setRel([...relations, { name: "", type: "belongs_to", resource: "", field: "" }])}><Icon name="plus" />Add relation</button>
      </Section>
      <Section title="Behaviour">
        <div className="form-grid">
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
      </Section>
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
      <Section title="Endpoint">
        <Field label="Method and path" hint="Served under /rest/v1. Use {param} for path parameters.">
          <div className="input-group">
            <select value={body.method || "POST"} onChange={(e) => patch({ method: e.target.value })} style={{ fontWeight: 700 }}>
              {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}
            </select>
            <span className="addon mono">/rest/v1</span>
            <input className="mono" autoFocus placeholder="/checkout" {...text(body, "path", patch)} />
          </div>
        </Field>
        <div className="form-grid">
          <Field label="Name" optional hint="The operation id in OpenAPI."><input className="mono" placeholder="checkout" {...text(body, "name", patch)} /></Field>
          <Field label="Description" optional><input {...text(body, "description", patch)} /></Field>
        </div>
        <Enabled body={body} patch={patch} hint="Disabled routes answer 404." />
      </Section>
      <Section title="Handler" description="What runs when the endpoint is called.">
        <div className="form-grid">
          <Field label="Type">
            <Segmented options={[["flow", "Flow"], ["function", "Function"]]} value={body.handler_type || "flow"} onChange={(handler_type) => patch({ handler_type, handler: "" })} />
          </Field>
          <Field label={body.handler_type === "function" ? "Function" : "Flow"} hint={handlers.length ? null : `No ${body.handler_type === "function" ? "functions" : "flows"} yet; type a name.`}>
            {handlers.length ? (
              <RefSelect options={handlers} value={body.handler} allowEmpty={false} placeholder="Choose" onChange={(handler) => patch({ handler })} />
            ) : (
              <input className="mono" {...text(body, "handler", patch)} />
            )}
          </Field>
        </div>
      </Section>
      <Section title="Access">
        <PolicyPicker value={body.policy} onChange={(policy) => patch({ policy })} policies={refs.policies} />
      </Section>
      <Section title="Request & response">
        <Field label="Request body">
          <Segmented options={[["none", "Anything"], ["fields", "Define fields"], ["schema", "Use a schema"]]} value={inputMode} onChange={setInput} />
        </Field>
        {inputMode === "fields" && <FieldsEditor value={body.input_fields || []} onChange={(input_fields) => patch({ input_fields })} schemas={refs.schemas} />}
        {inputMode === "schema" && <RefSelect options={refs.schemas} value={body.input_schema} allowEmpty={false} placeholder="Choose a schema" onChange={(input_schema) => patch({ input_schema })} />}
        <div className="form-grid">
          <Field label="Response schema" optional><RefSelect options={refs.schemas} value={body.response_schema} onChange={(response_schema) => patch({ response_schema })} /></Field>
          <Field label="Transformer" optional><RefSelect options={refs.transformers} value={typeof body.transformer === "string" ? body.transformer : null} onChange={(transformer) => patch({ transformer })} /></Field>
        </div>
      </Section>
      <Section title="Limits">
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
    <div className="form-grid">
      <Field label="Run">
        <Segmented options={types.map((v) => [v, labels[v].split(" ")[0]])} value={t} onChange={(target_type) => patch({ target_type, target: "" })} />
      </Field>
      <Field label={labels[t]}>
        {options && options.length ? (
          <RefSelect options={options} value={body.target} allowEmpty={false} placeholder="Choose" onChange={(target) => patch({ target })} />
        ) : (
          <input className="mono" placeholder={t === "realtime" ? "orders:{{ event.data.id }}" : t === "event" ? "billing.invoice_paid" : "name"} {...text(body, "target", patch)} />
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
      <Section title="When" description="Wildcards match a family: todos.* or user.*">
        <Field label="Event">
          <input className="mono" list="pb-events" placeholder="user.created" {...text(body, "event", patch)} />
          <datalist id="pb-events">{eventSuggestions(refs).map((e) => <option key={e} value={e} />)}</datalist>
        </Field>
        <Switch checked={conditional} onChange={(on) => { const t = toTree({ exists: "$event.data" }); setTree(t); patch({ condition: on ? { exists: "$event.data" } : null }); }} label="Only when…" hint="Filter on the event with rules over $event." />
        {conditional && <ConditionBuilder tree={tree} onChange={(t, condition) => { setTree(t); patch({ condition }); }} />}
      </Section>
      <Section title="Then">
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
      <Section title="Delivery">
        <Field label="Endpoint URL"><input className="mono" type="url" placeholder="https://example.com/hooks/pawabase" {...text(body, "url", patch)} /></Field>
        <Field label="Events" hint="* sends everything.">
          <TagInput value={body.events || []} onChange={(events) => patch({ events })} suggestions={["*", "user.*", ...refs.resources.map((r) => `${r}.*`)]} />
        </Field>
        <Field label="Extra headers" optional>
          <KeyValue value={body.headers || {}} onChange={(headers) => patch({ headers })} keyLabel="Header" valueLabel="Value" addLabel="Add header" />
        </Field>
      </Section>
      <Section title="Reliability & signing">
        <div className="form-grid">
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

const VERIFY = [["hmac-sha256", "HMAC-SHA256"], ["hmac-sha512", "HMAC-SHA512"], ["pawabase", "Pawabase signature"], ["token", "Shared token"], ["none", "None"]];

function InboundForm({ body, patch, refs, isNew, project, env }) {
  const verification = body.verification || "hmac-sha256";
  return (
    <>
      <Identity body={body} patch={patch} isNew={isNew} nameKey="slug" namePlaceholder="stripe" nameHint={`Received at /hooks/v1/${project.ref}/${env}/${body.slug || "<slug>"}`}>
        <Field label="Display name" optional><input placeholder="Stripe" {...text(body, "name", patch)} /></Field>
      </Identity>
      <Section title="Verification" description="Requests that fail verification are refused before anything runs.">
        <Segmented options={VERIFY} value={verification} onChange={(v) => patch({ verification: v })} />
        {verification !== "none" && (
          <div className="form-grid">
            {verification !== "pawabase" && (
              <Field label="Signature header"><input className="mono" placeholder="x-signature" {...text(body, "signature_header", patch)} /></Field>
            )}
            <Field label="Secret" optional hint={isNew ? "Leave empty to generate one." : "Leave empty to keep the current secret."}>
              <input type="password" autoComplete="new-password" value={body.secret || ""} onChange={(e) => patch({ secret: e.target.value || undefined })} />
            </Field>
          </div>
        )}
        {verification === "none" && <div className="alert warn">Anyone who knows the URL can trigger this hook.</div>}
      </Section>
      <Section title="Then">
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
      <Section title="When" description="Times are UTC.">
        <Segmented options={[["cron", "On a schedule"], ["interval", "Every…"]]} value={mode} onChange={(m) => patch(m === "cron" ? { cron: body.cron || "0 3 * * *", interval_seconds: null } : { cron: null, interval_seconds: 3600 })} />
        {mode === "cron" ? (
          <>
            <div className="chips">
              {CRON_PRESETS.map(([c, label]) => <button type="button" key={c} className={`chip ${body.cron === c ? "on" : ""}`} onClick={() => patch({ cron: c })}>{label}</button>)}
            </div>
            <Field label="Cron expression" hint={preset ? preset[1] : "minute hour day-of-month month day-of-week"}>
              <input className="mono" value={body.cron || ""} placeholder="0 3 * * *" onChange={(e) => patch({ cron: e.target.value })} />
            </Field>
          </>
        ) : (
          <Field label="Interval" hint="At least 10 seconds.">
            <div className="input-group" style={{ maxWidth: 320 }}>
              <input type="number" min="1" value={seconds / unit || ""} onChange={(e) => patch({ interval_seconds: Math.round(Number(e.target.value) * unit) || null })} />
              <select value={unit} onChange={(e) => { const u = Number(e.target.value); setUnit(u); patch({ interval_seconds: Math.round((seconds / unit) * u) }); }}>
                {UNITS.map(([u, label]) => <option key={u} value={u}>{label}</option>)}
              </select>
            </div>
          </Field>
        )}
      </Section>
      <Section title="Then">
        <Target body={body} patch={patch} refs={refs} types={["flow", "function", "event"]} />
        <Field label="Payload" optional hint="Free-form JSON passed as the input.">
          <JsonInput value={body.payload ?? undefined} rows={4} onChange={(payload) => patch({ payload })} />
        </Field>
        <Enabled body={body} patch={patch} />
      </Section>
    </>
  );
}

// ── buckets ───────────────────────────────────────────────────────────────

const MIME_PRESETS = ["image/*", "video/*", "audio/*", "application/pdf", "text/*"];
const MB = 1024 * 1024;

export function BucketForm({ body, patch, refs, isNew }) {
  return (
    <>
      <Section title="Details">
        <div className="form-grid">
          <Field label="Name" hint="Lower-case letters, digits and dashes.">
            <input className="mono" autoFocus={isNew} disabled={!isNew} placeholder="avatars" {...text(body, "name", patch)} />
          </Field>
          <Field label="Description" optional><input {...text(body, "description", patch)} /></Field>
        </div>
      </Section>
      <Section title="Access">
        <Switch checked={!!body.public} onChange={(v) => patch({ public: v })} label="Public bucket" hint="Anyone with a link may read objects; no signing needed." />
        <div className="form-grid">
          {!body.public && (
            <Field label="Who may read">
              <PolicyPicker value={body.read_policy} onChange={(read_policy) => patch({ read_policy })} policies={refs.policies} nullLabel="Default" />
            </Field>
          )}
          <Field label="Who may upload">
            <PolicyPicker value={body.write_policy} onChange={(write_policy) => patch({ write_policy })} policies={refs.policies} nullLabel="Default" />
          </Field>
        </div>
      </Section>
      <Section title="Uploads">
        <Field label="Accepted types" hint="MIME patterns. Empty accepts anything.">
          <TagInput value={body.accepts || []} onChange={(accepts) => patch({ accepts })} suggestions={MIME_PRESETS} placeholder="image/*" />
        </Field>
        <Field label="Largest file" hint="0 for no limit.">
          <div className="input-group" style={{ maxWidth: 220 }}>
            <input type="number" min="0" step="0.5" value={body.max_bytes ? +(body.max_bytes / MB).toFixed(2) : 0} onChange={(e) => patch({ max_bytes: Math.round(Number(e.target.value) * MB) })} />
            <span className="addon">MB</span>
          </div>
        </Field>
        <Switch checked={body.signed_uploads !== false} onChange={(signed_uploads) => patch({ signed_uploads })} label="Signed uploads" hint="Clients upload straight to storage with a short-lived URL." />
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
