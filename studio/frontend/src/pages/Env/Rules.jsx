// Rules tester: ask whether a caller may do something, and see exactly why.
//
// A playground rather than a form: the request reads as a sentence ("Can an admin list plans?"), the answer
// updates as you change it, and the tabs under it explain the answer (the checks behind it, the rows it lets
// through, every caller and operation side by side, and what the rule saw). Nothing is changed by testing.

import { useEffect, useMemo, useRef, useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Field, Segmented, Spinner, TagInput } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";
import "./rules/rules.css";

const OPERATIONS = ["list", "get", "create", "update", "delete"];
const WORDS = {
  eq: "equals", ne: "does not equal", gt: "is greater than", gte: "is at least", lt: "is less than", lte: "is at most",
  in: "is one of", not_in: "is not one of", contains: "contains", starts_with: "starts with", ends_with: "ends with", matches: "matches",
};
const UNARY_WORDS = { exists: "exists", truthy: "is set", empty: "is empty" };
// Who can be asked about, in one click. `roles` of null means "whatever the user really has".
const PRESETS = [
  { key: "anonymous", label: "Anonymous", kind: "anonymous", roles: null },
  { key: "user", label: "Signed in", kind: "user", roles: [] },
  { key: "admin", label: "Admin", kind: "user", roles: ["admin"] },
  { key: "service", label: "Service key", kind: "service", roles: null },
];

const show = (value) => (value === undefined || value === null ? "nothing" : JSON.stringify(value));

function parseReference(text) {
  const trimmed = text.trim();
  if (!trimmed) return { value: null };
  if (/^[{[]|^(true|false)$/.test(trimmed)) {
    try { return { value: JSON.parse(trimmed) }; } catch (e) { return { error: `Not valid JSON: ${e.message}` }; }
  }
  return { value: trimmed };
}

function parseJson(text, label) {
  if (!text.trim()) return { value: null };
  try {
    const value = JSON.parse(text);
    return typeof value === "object" && value !== null && !Array.isArray(value) ? { value } : { error: `${label} must be a JSON object` };
  } catch (e) { return { error: `${label}: ${e.message}` }; }
}

// ── the explanation ──────────────────────────────────────────────────

function Operand({ operand }) {
  return operand.expr
    ? <span className="rt-operand"><code>{operand.expr}</code><em>{show(operand.value)}</em></span>
    : <span className="rt-operand"><code>{show(operand.value)}</code></span>;
}

function label(node) {
  if (node.op === "literal") return node.result ? "Always allowed" : "Never allowed";
  if (node.op === "all") return "All of these";
  if (node.op === "any") return "Any of these";
  if (node.op === "not") return "None of these";
  if (node.op === "service" || node.op === "python") return node.note;
  if (WORDS[node.op]) return <span className="rt-sentence"><Operand operand={node.left} /> {WORDS[node.op]} <Operand operand={node.right} /></span>;
  if (UNARY_WORDS[node.op]) return <span className="rt-sentence"><Operand operand={node.operand} /> {UNARY_WORDS[node.op]}</span>;
  return <span className="rt-sentence"><b>{node.op}</b>{node.arg !== undefined && <> <code>{typeof node.arg === "string" ? node.arg : JSON.stringify(node.arg)}</code></>}</span>;
}

function TraceNode({ node }) {
  const tone = node.result === true ? "pass" : node.result === false ? "fail" : "info";
  return (
    <li className={`rt-node ${tone}`}>
      <div className="rt-row">
        <span className="rt-mark">{node.result === true ? <Icon name="check" size={13} /> : node.result === false ? <Icon name="x" size={13} /> : "·"}</span>
        <span className="rt-label">{label(node)}</span>
      </div>
      {node.children?.length > 0 && <ul className="rt-children">{node.children.map((child, i) => <TraceNode key={i} node={child} />)}</ul>}
    </li>
  );
}

const Trace = ({ node }) => <ul className="rt-tree"><TraceNode node={node} /></ul>;

function Verdict({ out, busy }) {
  const tone = out.allowed ? "ok" : out.status === 404 ? "warn" : "bad";
  const headline = out.enabled === false ? "No such route" : out.allowed ? "Allowed" : out.status === 404 ? "Hidden" : "Refused";
  return (
    <div className={`rt-verdict ${tone} ${busy ? "busy" : ""}`}>
      <span className="rt-verdict-icon"><Icon name={out.allowed ? "check" : "x"} size={20} /></span>
      <div className="rt-verdict-text">
        <div className="rt-verdict-main"><b>{headline}</b><span className="rt-status">HTTP {out.status}</span>{out.policy && <Badge>{out.policy}</Badge>}</div>
        <p>{out.reason}</p>
      </div>
    </div>
  );
}

function Rows({ preview, plan }) {
  const [open, setOpen] = useState(false);
  const filters = Object.entries(plan?.filters || {});
  return (
    <div className="stack lg">
      <div className="rt-counts">
        <div className="ok"><b>{preview.visible}</b><span>visible</span></div>
        <div className="bad"><b>{preview.hidden}</b><span>hidden by the rule</span></div>
        <div><b>{preview.table_rows_matching_filters ?? preview.scanned}</b><span>in the table{preview.complete ? "" : " (first rows read)"}</span></div>
      </div>
      {(filters.length > 0 || plan?.residual || plan?.python) && (
        <p className="hint">
          {filters.length > 0 && <>Filtered in the database: <code>{filters.map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join(" and ")}</code>. </>}
          {plan.residual && <>Each remaining row is then checked against the rest of the rule.</>}
          {plan.python && <>Each remaining row is then checked by the policy's Python code.</>}
        </p>
      )}
      {preview.sample.length > 0 && <div><div className="rt-subhead">First visible rows</div><pre className="code-block rt-json">{JSON.stringify(preview.sample, null, 2)}</pre></div>}
      {preview.hidden_example && (
        <div className="rt-example">
          <button type="button" className="linklike" onClick={() => setOpen(!open)}>{open ? "Hide" : "Why was a row hidden?"}</button>
          {open && (<><pre className="code-block rt-json">{JSON.stringify(preview.hidden_example.row, null, 2)}</pre><Trace node={preview.hidden_example.trace} /></>)}
        </div>
      )}
    </div>
  );
}

/** Every operation for several callers at once. A cell is a click away from testing that exact case. */
function Matrix({ cells, busy, active, onPick }) {
  return (
    <div className="rt-matrix-wrap">
      <table className="rt-matrix">
        <thead><tr><th />{PRESETS.map((p) => <th key={p.key}>{p.label}</th>)}</tr></thead>
        <tbody>
          {OPERATIONS.map((op) => (
            <tr key={op}>
              <th>{op}</th>
              {PRESETS.map((p) => {
                const cell = cells?.[`${p.key}:${op}`];
                const tone = !cell ? "wait" : cell.enabled === false ? "off" : cell.allowed ? "ok" : cell.status === 404 ? "warn" : "bad";
                const on = active.preset === p.key && active.operation === op;
                return (
                  <td key={p.key}>
                    <button type="button" className={`rt-cell ${tone} ${on ? "on" : ""}`} onClick={() => onPick(p, op)} title={cell ? `${p.label} · ${op}: ${cell.reason || ""}` : "Testing…"} disabled={!cell}>
                      {!cell ? "·" : cell.enabled === false ? "off" : cell.allowed ? <Icon name="check" size={14} /> : cell.status === 404 ? "404" : <Icon name="x" size={14} />}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">{busy ? "Testing every case…" : "Click a cell to test that caller and operation. Operations that read a record use the record from the Data tab."}</p>
    </div>
  );
}

// ── the page ─────────────────────────────────────────────────────────

export default function Rules({ env }) {
  const base = envPath(env);
  const resources = useApi(`${base}/resources`, { params: { limit: 500 } });
  const policies = useApi(`${base}/policies`);
  const initial = useMemo(() => new URLSearchParams(window.location.search), []);

  const [mode, setMode] = useState(initial.get("policy") ? "policy" : "resource");
  const [resource, setResource] = useState(initial.get("resource") || "");
  const [operation, setOperation] = useState(initial.get("operation") || "list");
  const [policyText, setPolicyText] = useState(initial.get("policy") || "authenticated");
  const [kind, setKind] = useState("user");
  const [roles, setRoles] = useState([]);
  const [permissions, setPermissions] = useState([]);
  const [picked, setPicked] = useState(null);
  const [userId, setUserId] = useState("");
  const [search, setSearch] = useState("");
  const [found, setFound] = useState([]);
  const [org, setOrg] = useState("");
  const [orgRole, setOrgRole] = useState("");
  const [aal, setAal] = useState("aal1");
  const [recordId, setRecordId] = useState("");
  const [recordText, setRecordText] = useState("");
  const [inputText, setInputText] = useState("");
  const [side, setSide] = useState("caller");
  const [tab, setTab] = useState("why");

  const [out, setOut] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = useRef(0);

  const list = resources.data?.data || [];
  useEffect(() => { if (!resource && list.length) setResource(list[0].name); }, [list.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const selected = list.find((r) => r.name === resource);
  const configured = selected?.operations?.[operation];

  useEffect(() => {
    if (search.trim().length < 2 || picked) { setFound([]); return undefined; }
    const t = setTimeout(async () => {
      try { setFound((await get(`/envs/${env}/users`, { service: "auth", params: { search: search.trim(), limit: 6 } }))?.data || []); } catch { setFound([]); }
    }, 200);
    return () => clearTimeout(t);
  }, [env, search, picked]);

  const needsRecord = mode === "resource" && ["get", "update", "delete"].includes(operation);
  const needsInput = mode === "resource" && ["create", "update"].includes(operation);
  const dataTab = needsRecord || needsInput;
  useEffect(() => { if (side === "data" && !dataTab) setSide("caller"); }, [dataTab, side]);
  useEffect(() => { if (tab === "rows" && !(out?.preview)) setTab("why"); }, [out, tab]);
  useEffect(() => { if (tab === "matrix" && mode !== "resource") setTab("why"); }, [mode, tab]);

  const presetKey = kind === "anonymous" ? "anonymous" : kind === "service" ? "service" : !picked && roles.length === 0 && !permissions.length ? "user" : !picked && roles.length === 1 && roles[0] === "admin" && !permissions.length ? "admin" : null;
  const applyPreset = (preset) => {
    setKind(preset.kind);
    if (preset.kind === "user") { setRoles(preset.roles || []); setPicked(null); setUserId(""); setSearch(""); }
  };

  // The data the request carries, or why there is no request yet.
  const data = useMemo(() => {
    const record = parseJson(recordText, "Record");
    const input = parseJson(inputText, "Submitted data");
    const reference = parseReference(policyText);
    const problem = record.error || input.error || (mode === "policy" && (reference.error || (reference.value === null ? "Name a policy or write a condition." : null)));
    return { record: record.value, input: input.value, reference: reference.value, problem };
  }, [mode, policyText, recordText, inputText]);

  const caller = useMemo(() => (kind === "user"
    ? { kind, user_id: picked?.id || userId.trim() || null, roles: roles.length ? roles : picked ? null : [], permissions: permissions.length ? permissions : null, org: org.trim() || null, org_role: orgRole.trim() || null, aal }
    : { kind }), [kind, picked, userId, roles, permissions, org, orgRole, aal]);

  const bodyFor = (who, op) => ({
    ...(mode === "resource" ? { resource, operation: op } : { policy: data.reference }),
    caller: who,
    record: data.record,
    record_id: mode === "resource" && recordId.trim() ? recordId.trim() : null,
    input: data.input,
  });

  const key = JSON.stringify([mode, resource, operation, data, caller, recordId]);
  const execute = async () => {
    if (data.problem || (mode === "resource" && !resource)) { setError(data.problem || null); return; }
    const mine = ++run.current;
    setBusy(true);
    try {
      const answer = await post(`${base}/policies/test`, bodyFor(caller, operation));
      if (mine === run.current) { setOut(answer); setError(null); }
    } catch (e) {
      if (mine === run.current) { setError(e.message); setOut(null); }
    } finally {
      if (mine === run.current) setBusy(false);
    }
  };
  useEffect(() => { const t = setTimeout(execute, 400); return () => clearTimeout(t); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  // The matrix: every preset caller × every operation, only while its tab is open.
  const [cells, setCells] = useState(null);
  const [matrixBusy, setMatrixBusy] = useState(false);
  const matrixRun = useRef(0);
  const matrixKey = JSON.stringify([resource, data.record, data.input, recordId, aal]);
  useEffect(() => {
    if (tab !== "matrix" || mode !== "resource" || !resource || data.problem) return undefined;
    const t = setTimeout(async () => {
      const mine = ++matrixRun.current;
      setMatrixBusy(true);
      const next = {};
      await Promise.all(PRESETS.flatMap((p) => OPERATIONS.map(async (op) => {
        try {
          next[`${p.key}:${op}`] = await post(`${base}/policies/test`, bodyFor(p.kind === "user" ? { kind: "user", user_id: null, roles: p.roles, aal } : { kind: p.kind }, op));
        } catch (e) { next[`${p.key}:${op}`] = { allowed: false, status: 0, reason: e.message }; }
      })));
      if (mine === matrixRun.current) { setCells(next); setMatrixBusy(false); }
    }, 300);
    return () => clearTimeout(t);
  }, [tab, mode, matrixKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const pickCell = (preset, op) => { applyPreset(preset); setOperation(op); setTab("why"); };
  const tabs = [["why", "Why"], ...(out?.preview ? [["rows", "Rows"]] : []), ...(mode === "resource" ? [["matrix", "Everyone"]] : []), ["context", "What the rule saw"], ["rule", "Rule"]];

  return (
    <Layout title="Rules tester">
      <div className="rt-app">
        <header className="rt-head">
          <div className="rt-ask">
            <span className="rt-word">Can</span>
            <div className="rt-presets" role="radiogroup" aria-label="Who is asking">
              {PRESETS.map((p) => <button key={p.key} type="button" role="radio" aria-checked={presetKey === p.key} className={presetKey === p.key ? "on" : ""} onClick={() => applyPreset(p)}>{p.label}</button>)}
              {presetKey === null && <button type="button" role="radio" aria-checked className="on">{picked ? picked.email : "Custom"}</button>}
            </div>
            {mode === "resource" ? (
              <>
                <span className="rt-word">do</span>
                <Segmented value={operation} onChange={setOperation} options={OPERATIONS.map((o) => [o, o])} />
                <span className="rt-word">on</span>
                <select className="rt-resource" value={resource} onChange={(e) => setResource(e.target.value)} aria-label="Resource">
                  {list.length === 0 && <option value="">No resources yet</option>}
                  {list.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
                </select>
              </>
            ) : (
              <>
                <span className="rt-word">pass</span>
                <input className="rt-policy mono" list="rt-policies" value={policyText} onChange={(e) => setPolicyText(e.target.value)} placeholder="owner, role:admin, or a JSON condition" spellCheck={false} aria-label="Policy" />
                <datalist id="rt-policies">{(policies.data?.data || []).map((p) => <option key={p.name} value={p.name} />)}</datalist>
              </>
            )}
            <span className="rt-word">?</span>
            <button type="button" className="linklike rt-switch" onClick={() => setMode(mode === "resource" ? "policy" : "resource")}>{mode === "resource" ? "Test a policy instead" : "Test a resource instead"}</button>
          </div>
          {mode === "resource" && selected && (
            <p className="rt-guard">
              {configured?.enabled ? <>{operation} is guarded by <code>{typeof configured.policy === "string" ? configured.policy : JSON.stringify(configured.policy || "authenticated")}</code></> : <>{operation} is not enabled on {selected.name}</>}
            </p>
          )}
        </header>

        <div className="rt-body">
          <aside className="rt-side">
            <div className="rt-tabs">
              <button type="button" className={side === "caller" ? "on" : ""} onClick={() => setSide("caller")}>Caller</button>
              {dataTab && <button type="button" className={side === "data" ? "on" : ""} onClick={() => setSide("data")}>Data</button>}
            </div>
            <div className="rt-side-body">
              {side === "caller" && (kind === "user" ? (
                <div className="stack">
                  <Field label="User" hint="Search to load a real user's roles and permissions, or type any id.">
                    {picked ? (
                      <div className="rt-picked"><b>{picked.email}</b><button type="button" className="linklike" onClick={() => { setPicked(null); setUserId(""); setSearch(""); }}>Change</button></div>
                    ) : (
                      <div className="rt-search">
                        <input value={search} onChange={(e) => { setSearch(e.target.value); setUserId(e.target.value); }} placeholder="Email, or an id" />
                        {found.length > 0 && (
                          <div className="rt-found">
                            {found.map((u) => <button key={u.id} type="button" onMouseDown={(e) => { e.preventDefault(); setPicked(u); setUserId(u.id); setSearch(""); setRoles([]); }}><b>{u.email}</b><small>{u.id}</small></button>)}
                          </div>
                        )}
                      </div>
                    )}
                  </Field>
                  <Field label="Roles" optional={!picked} hint={picked ? "Empty uses this user's own roles." : undefined}><TagInput value={roles} onChange={setRoles} placeholder="admin, editor" /></Field>
                  <Field label="Permissions" optional><TagInput value={permissions} onChange={setPermissions} placeholder="posts.write" /></Field>
                  <div className="row top" style={{ gap: 10 }}>
                    <Field label="Organization" optional className="grow"><input value={org} onChange={(e) => setOrg(e.target.value)} placeholder="acme" /></Field>
                    <Field label="Org role" optional className="grow"><input value={orgRole} onChange={(e) => setOrgRole(e.target.value)} placeholder="admin" /></Field>
                  </div>
                  <Field label="Sign-in strength"><Segmented value={aal} onChange={setAal} options={[["aal1", "Password"], ["aal2", "Second factor"]]} /></Field>
                </div>
              ) : (
                <p className="hint rt-note">{kind === "anonymous" ? "Not signed in: no user, no roles. Most rules start by requiring a signed-in user." : "Secret keys and Studio bypass policies, so this always passes. Use it to confirm that is what you meant."}</p>
              ))}
              {side === "data" && (
                <div className="stack">
                  {needsRecord && (
                    <>
                      <Field label="Record id" optional hint="Loads the real record from the table."><input className="mono" value={recordId} onChange={(e) => setRecordId(e.target.value)} placeholder="01HX…" /></Field>
                      <Field label="Or a record as JSON" optional hint="Used when no id is given."><textarea rows={5} className="mono" value={recordText} onChange={(e) => setRecordText(e.target.value)} placeholder='{"owner_id": "…"}' spellCheck={false} /></Field>
                    </>
                  )}
                  {needsInput && <Field label="Submitted data" optional hint="What the request body would contain."><textarea rows={5} className="mono" value={inputText} onChange={(e) => setInputText(e.target.value)} placeholder='{"title": "Hello"}' spellCheck={false} /></Field>}
                </div>
              )}
            </div>
          </aside>

          <section className="rt-result">
            {error && <div className="alert error">{error}</div>}
            {!out && !error && <div className="empty">{busy ? <Spinner /> : "Choose who is asking and what for."}</div>}
            {out && (
              <>
                <Verdict out={out} busy={busy} />
                {out.warnings?.map((w) => <div key={w} className="alert warn">{w}</div>)}
                <div className="rt-tabs wide">
                  {tabs.map(([k, l]) => <button key={k} type="button" className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
                </div>
                <div className={`rt-panel ${busy ? "rt-stale" : ""}`}>
                  {tab === "why" && (out.trace ? <Trace node={out.trace} /> : <p className="hint">Nothing to explain: this operation is not enabled.</p>)}
                  {tab === "rows" && out.preview && <Rows preview={out.preview} plan={out.plan} />}
                  {tab === "matrix" && <Matrix cells={cells} busy={matrixBusy} active={{ preset: presetKey, operation }} onPick={pickCell} />}
                  {tab === "context" && (
                    <>
                      <p className="hint">{out.caller?.source === "user" ? "Loaded from this user." : out.caller?.source === "manual" ? "Values you entered." : "The caller as the rule sees it."}</p>
                      <pre className="code-block rt-json">{JSON.stringify(out.caller?.auth || {}, null, 2)}</pre>
                    </>
                  )}
                  {tab === "rule" && (
                    <>
                      <p className="hint">The condition behind <code>{out.policy}</code>.</p>
                      <pre className="code-block rt-json">{out.condition === null || out.condition === undefined ? "Decided by Python code." : JSON.stringify(out.condition, null, 2)}</pre>
                    </>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </Layout>
  );
}
