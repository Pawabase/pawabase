// Who may do what with one table: the Access tab of Data → Tables.
//
// A table is reachable through the API only when it has a resource, and each of the resource's five operations has its own policy:
// a built-in, one of the environment's named policies, a role or permission, a rule on the record's own fields, or several of them
// together. This tab keeps that whole policy system, adds one-click set-ups, and warns about choices that open a table too far.

import { Link } from "@inertiajs/react";
import { useEffect, useState } from "react";
import { Badge, Button, Field, Spinner, useAction, useToast } from "./ui";
import { ResourceForm, problem } from "./definitions/forms";
import { useRefs } from "./definitions/refs";
import { editable } from "../lib/kinds";
import { envPath, post, put, useApi } from "../lib/api";
import { envHref } from "./Layout";

const OPS = ["list", "get", "create", "update", "delete"];
const WRITES = ["create", "update", "delete"];
const NAMES = { list: "See the list", get: "Open one record", create: "Add records", update: "Edit records", delete: "Delete records" };
const BUILTIN = ["public", "authenticated", "owner", "mfa", "service", "deny"];
const ALL = (policy) => Object.fromEntries(OPS.map((op) => [op, { enabled: true, policy }]));

/** The common set-ups. Each is a full set of five operations, so choosing one is a complete answer, not a partial edit. */
const PRESETS = [
  ["private", "Private: secret keys and Studio only", ALL("service")],
  ["members", "Signed-in users can read and change", ALL("authenticated")],
  ["public-read", "Anyone reads, signed-in users change", { ...ALL("authenticated"), list: { enabled: true, policy: "public" }, get: { enabled: true, policy: "public" } }],
  ["owner", "Each user sees and changes their own records", { ...ALL("owner"), create: { enabled: true, policy: "authenticated" } }],
];

const same = (a, b) => OPS.every((op) => !!a?.[op]?.enabled === !!b[op].enabled && JSON.stringify(a?.[op]?.policy ?? null) === JSON.stringify(b[op].policy ?? null));

function quickSetOf(operations) {
  const preset = PRESETS.find(([, , ops]) => same(operations, ops));
  if (preset) return preset[0];
  const first = operations?.list?.policy;
  if (typeof first === "string" && OPS.every((op) => operations?.[op]?.enabled && operations[op].policy === first)) return `policy:${first}`;
  return "custom";
}

/** What is worth a second look in the current choices, in order of how much it matters. */
function warnings(operations, known) {
  const out = [];
  for (const op of OPS) {
    const setting = operations?.[op];
    if (!setting?.enabled) continue;
    const policy = setting.policy;
    if (policy === "public" && WRITES.includes(op)) out.push(["red", `Anyone on the internet, signed in or not, can ${NAMES[op].toLowerCase()}.`]);
    else if (typeof policy === "string" && !policy.includes(":") && !BUILTIN.includes(policy) && !known.includes(policy)) out.push(["red", `“${policy}” is not a policy in this environment, so “${NAMES[op]}” will be refused when you save.`]);
    else if ((policy === null || policy === undefined) && WRITES.includes(op)) out.push(["amber", `${NAMES[op]} has no rule of its own, so any signed-in user can. Pick one to be explicit.`]);
  }
  if (OPS.every((op) => !operations?.[op]?.enabled)) out.push(["blue", "Every action is off, so this table is not served through the API. Only Studio and secret keys reach it."]);
  return out;
}

export default function TableAccess({ env, table, resource, onDefine, onChanged }) {
  const path = resource ? envPath(env, `/resources/${resource}`) : null;
  const item = useApi(path);
  const refs = useRefs(env);
  const toast = useToast();
  const [draft, setDraft] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [run, busy] = useAction();
  useEffect(() => { if (item.data) { const next = editable(item.data); setDraft(next); setBaseline(next); } }, [item.data]);

  if (!resource) {
    return (
      <div className="tbl-state flex flex-col items-center justify-center gap-3 p-[48px_16px] text-muted text-ui text-center">
        <Badge>Not in the API</Badge>
        <span style={{ maxWidth: 420 }}><b className="mono font-mono text-ui-sm">{table}</b> has no API of its own yet, so your app's users can't reach it. Only Studio and secret keys can.</span>
        <Button variant="primary" onClick={onDefine}>Define its columns to open it up</Button>
      </div>
    );
  }
  if (!item.data || !draft) return <div className="tbl-state flex items-center justify-center gap-2 p-[48px_16px] text-muted text-ui"><Spinner /></div>;

  const operations = draft.operations || {};
  const quick = quickSetOf(operations);
  const dirty = JSON.stringify(draft.operations || {}) !== JSON.stringify(baseline?.operations || {});
  const issue = problem("resources", draft);
  const notes = warnings(operations, refs.policies);
  const apply = (preset) => setDraft((d) => ({ ...d, operations: { ...(d.operations || {}), ...Object.fromEntries(OPS.map((op) => [op, { ...(d.operations?.[op] || {}), ...preset[op] }])) } }));
  const choose = (key) => {
    if (key === "custom") return;
    if (key.startsWith("policy:")) apply(ALL(key.slice(7)));
    else apply(PRESETS.find(([k]) => k === key)[2]);
  };
  const save = async () => { if (await run(() => put(path, draft), "Access saved")) { item.reload(); onChanged?.(); } };
  // A rule written in place becomes a named policy the whole environment can reuse, and the action switches to point at it.
  const savePolicy = async (name, condition, use) => {
    const saved = await run(() => post(envPath(env, "/policies"), { name, description: `Saved from ${table}`, condition }));
    if (!saved) return false;
    refs.reload();
    use(name);
    toast(`Saved policy ${name}`);
    return true;
  };

  return (
    <div className="resource-settings flex-none w-full min-h-[min-content] p-[0_var(--gutter)_48px] max-mobile:p-[0_16px_32px]">
      <div className="w-[min(100%,_720px)] m-[0_auto]">
        <div className="flex items-center justify-between gap-4 min-h-[68px] [border-top:1px_solid_var(--line)]">
          <div className="flex flex-col gap-0.5"><b>Access</b><span className="muted text-muted">Who can see and change <b className="mono font-mono text-ui-sm">{table}</b> through your API.</span></div>
          <div className="row flex items-center gap-2">
            <span className="hint text-xs font-book text-muted leading-[1.45]">{issue}</span>
            {dirty && <span className="dirty-pill p-[3px_9px] [border:1px_solid_var(--brand)] rounded-full bg-brand-soft text-brand-strong text-caption font-semibold whitespace-nowrap">Unsaved changes</span>}
            <Button size="sm" variant="primary" disabled={busy || !dirty || !!issue} onClick={save}>Save access</Button>
          </div>
        </div>

        <Field label="Quick set" hint="Fills in all five actions at once. You can still change each one below.">
          <select value={quick} onChange={(e) => choose(e.target.value)}>
            <optgroup label="Set-ups">{PRESETS.map(([k, name]) => <option key={k} value={k}>{name}</option>)}</optgroup>
            {refs.policies.length > 0 && <optgroup label="Use one policy for everything">{refs.policies.map((name) => <option key={name} value={`policy:${name}`}>{name}</option>)}</optgroup>}
            {quick === "custom" && <option value="custom">Custom (set per action)</option>}
          </select>
        </Field>

        <div style={{ marginTop: 8 }}>
          <ResourceForm body={draft} patch={(changes) => setDraft((d) => ({ ...d, ...changes }))} refs={refs} isNew={false} mode="access" onSavePolicy={savePolicy} />
        </div>

        {notes.length > 0 && (
          <div className="stack flex flex-col gap-2" style={{ marginTop: 12 }}>
            {notes.map(([tone, text], i) => <div key={i} className={`alert ${tone === "red" ? "error" : tone === "amber" ? "warn" : ""}`}>{text}</div>)}
          </div>
        )}

        <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ marginTop: 16 }}>
          Secret keys and Studio always get through. Reusable rules live under <Link href={envHref(env, "policies")}>Policies</Link>; once saved there they appear in every list above.
        </p>
      </div>
    </div>
  );
}
