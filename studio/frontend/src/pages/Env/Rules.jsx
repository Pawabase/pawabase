// Rules tester: ask whether a caller may do something, and see exactly why.
//
// Runs the environment's live policies through the same engine the data API uses, for a real user, a made-up one,
// anonymous, or a service, against a real record or one typed in. For a list it also shows which rows the caller would
// see. Results update as the request changes.

import { useEffect, useMemo, useRef, useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, PageHead, Segmented, Spinner, TagInput } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";
import "./rules/rules.css";

const OPERATIONS = ["list", "get", "create", "update", "delete"];
const OPERATION_HINT = {
  list: "Which rows the caller can list",
  get: "Reading one record",
  create: "Creating a record with the data below",
  update: "Changing a record with the data below",
  delete: "Deleting a record",
};
const WORDS = {
  eq: "equals", ne: "does not equal", gt: "is greater than", gte: "is at least", lt: "is less than", lte: "is at most",
  in: "is one of", not_in: "is not one of", contains: "contains", starts_with: "starts with", ends_with: "ends with", matches: "matches",
};
const UNARY_WORDS = { exists: "exists", truthy: "is set", empty: "is empty" };

const show = (value) => (typeof value === "string" ? JSON.stringify(value) : value === undefined ? "nothing" : value === null ? "nothing" : JSON.stringify(value));

/** The policy reference typed in: a JSON condition, or a name like `owner` or `role:admin`. */
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
  if (node.op === "service") return node.note;
  if (node.op === "python") return node.note;
  if (WORDS[node.op]) return <span className="rt-sentence"><Operand operand={node.left} /> {WORDS[node.op]} <Operand operand={node.right} /></span>;
  if (UNARY_WORDS[node.op]) return <span className="rt-sentence"><Operand operand={node.operand} /> {UNARY_WORDS[node.op]}</span>;
  return <span className="rt-sentence"><b>{node.op}</b>{node.arg !== undefined && <> <code>{typeof node.arg === "string" ? node.arg : JSON.stringify(node.arg)}</code></>}</span>;
}

function TraceNode({ node, depth = 0 }) {
  const tone = node.result === true ? "pass" : node.result === false ? "fail" : "info";
  return (
    <li className={`rt-node ${tone}`}>
      <div className="rt-row">
        <span className="rt-mark">{node.result === true ? <Icon name="check" size={13} /> : node.result === false ? <Icon name="x" size={13} /> : "·"}</span>
        <span className="rt-label">{label(node)}</span>
      </div>
      {node.children?.length > 0 && <ul className="rt-children">{node.children.map((child, i) => <TraceNode key={i} node={child} depth={depth + 1} />)}</ul>}
    </li>
  );
}

function Trace({ node }) {
  return <ul className="rt-tree"><TraceNode node={node} /></ul>;
}

function Verdict({ out }) {
  const tone = out.allowed ? "ok" : out.status === 404 ? "warn" : "bad";
  const headline = out.enabled === false ? "No such route" : out.allowed ? "Allowed" : out.status === 404 ? "Hidden" : "Refused";
  return (
    <div className={`rt-verdict ${tone}`}>
      <div className="rt-verdict-main">
        <b>{headline}</b>
        <span className="rt-status">HTTP {out.status}</span>
        {out.policy && <Badge>{out.policy}</Badge>}
      </div>
      <p>{out.reason}</p>
    </div>
  );
}

function Preview({ preview, plan }) {
  const [open, setOpen] = useState(false);
  return (
    <Card icon="eye" title="What this caller would see" description={`Read ${preview.scanned} of ${preview.table_rows_matching_filters ?? preview.scanned} rows${preview.complete ? "" : " (the first rows only)"}.`}>
      <div className="rt-counts">
        <div><b>{preview.visible}</b><span>visible</span></div>
        <div><b>{preview.hidden}</b><span>hidden by the rule</span></div>
      </div>
      {plan && (plan.filters && Object.keys(plan.filters).length > 0 || plan.residual) && (
        <p className="hint">
          {Object.keys(plan.filters || {}).length > 0 && <>Filtered in the database: <code>{Object.entries(plan.filters).map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join(" and ")}</code>. </>}
          {plan.residual && <>Then each row is checked against the rest of the rule.</>}
          {plan.python && <>Then each row is checked by the policy's Python code.</>}
        </p>
      )}
      {preview.sample.length > 0 && <pre className="code-block rt-json">{JSON.stringify(preview.sample, null, 2)}</pre>}
      {preview.hidden_example && (
        <div className="rt-example">
          <button type="button" className="linklike" onClick={() => setOpen(!open)}>{open ? "Hide" : "Why was a row hidden?"}</button>
          {open && (
            <>
              <pre className="code-block rt-json">{JSON.stringify(preview.hidden_example.row, null, 2)}</pre>
              <Trace node={preview.hidden_example.trace} />
            </>
          )}
        </div>
      )}
    </Card>
  );
}

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
  const [userId, setUserId] = useState("");
  const [picked, setPicked] = useState(null);
  const [roles, setRoles] = useState(null);
  const [permissions, setPermissions] = useState(null);
  const [org, setOrg] = useState("");
  const [orgRole, setOrgRole] = useState("");
  const [aal, setAal] = useState("aal1");
  const [recordId, setRecordId] = useState("");
  const [recordText, setRecordText] = useState("");
  const [inputText, setInputText] = useState("");
  const [search, setSearch] = useState("");
  const [found, setFound] = useState([]);

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

  // The request, or why there isn't one yet.
  const request = useMemo(() => {
    const record = parseJson(recordText, "Record");
    const input = parseJson(inputText, "Submitted data");
    const reference = parseReference(policyText);
    const problem = record.error || input.error || (mode === "policy" && (reference.error || (reference.value === null ? "Name a policy or write a condition." : null)));
    if (problem) return { problem };
    const caller = kind === "user"
      ? { kind, user_id: picked?.id || userId.trim() || null, roles, permissions, org: org.trim() || null, org_role: orgRole.trim() || null, aal }
      : { kind };
    return {
      body: {
        ...(mode === "resource" ? { resource, operation } : { policy: reference.value }),
        caller,
        record: record.value,
        record_id: mode === "resource" && recordId.trim() ? recordId.trim() : null,
        input: input.value,
      },
    };
  }, [mode, resource, operation, policyText, kind, userId, picked, roles, permissions, org, orgRole, aal, recordId, recordText, inputText]);

  const key = JSON.stringify(request);
  const execute = async () => {
    if (request.problem || (mode === "resource" && !resource)) { setError(request.problem || null); return; }
    const mine = ++run.current;
    setBusy(true);
    try {
      const answer = await post(`${base}/policies/test`, request.body);
      if (mine === run.current) { setOut(answer); setError(null); }
    } catch (e) {
      if (mine === run.current) { setError(e.message); setOut(null); }
    } finally {
      if (mine === run.current) setBusy(false);
    }
  };
  useEffect(() => {
    const t = setTimeout(execute, 450);
    return () => clearTimeout(t);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const needsRecord = mode === "resource" && ["get", "update", "delete"].includes(operation);
  const needsInput = mode === "resource" && ["create", "update"].includes(operation);

  return (
    <Layout title="Rules tester">
      <PageHead
        title="Rules tester"
        description="Ask whether a caller may do something and see why, using your live policies. Nothing is changed."
        actions={<Button onClick={execute} disabled={busy}><Icon name="play" />{busy ? "Testing…" : "Run again"}</Button>}
      />
      <div className="rt-page">
        <div className="rt-form">
          <Card icon="policies" title="What is being asked">
            <div className="stack">
              <Segmented value={mode} onChange={setMode} options={[["resource", "A resource's operation"], ["policy", "A policy"]]} />
              {mode === "resource" ? (
                <>
                  <Field label="Resource">
                    <select value={resource} onChange={(e) => setResource(e.target.value)}>
                      {list.length === 0 && <option value="">No resources yet</option>}
                      {list.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
                    </select>
                  </Field>
                  <Field label="Operation" hint={OPERATION_HINT[operation]}>
                    <Segmented value={operation} onChange={setOperation} options={OPERATIONS.map((o) => [o, o])} />
                  </Field>
                  {selected && (
                    <p className="hint">
                      {configured?.enabled
                        ? <>Guarded by <code>{typeof configured.policy === "string" ? configured.policy : JSON.stringify(configured.policy || "authenticated")}</code>.</>
                        : <>{operation} is not enabled on this resource.</>}
                    </p>
                  )}
                </>
              ) : (
                <Field label="Policy" hint="A name (owner, role:admin, permission:posts.write), or a JSON condition.">
                  <textarea rows={4} className="mono" value={policyText} onChange={(e) => setPolicyText(e.target.value)} list="rt-policies" spellCheck={false} />
                  <datalist id="rt-policies">{(policies.data?.data || []).map((p) => <option key={p.name} value={p.name} />)}</datalist>
                </Field>
              )}
            </div>
          </Card>

          <Card icon="users" title="Who is asking">
            <div className="stack">
              <Segmented value={kind} onChange={setKind} options={[["anonymous", "Anonymous"], ["user", "A user"], ["service", "Service key"]]} />
              {kind === "service" && <p className="hint">Secret keys and Studio bypass policies, so this always passes. Use it to confirm that is what you meant.</p>}
              {kind === "user" && (
                <>
                  <Field label="User" hint="Search your users to load their real roles and permissions, or type any id to test as someone else.">
                    {picked ? (
                      <div className="rt-picked"><b>{picked.email}</b><button type="button" className="linklike" onClick={() => { setPicked(null); setUserId(""); setSearch(""); }}>Change</button></div>
                    ) : (
                      <div className="rt-search">
                        <input value={search} onChange={(e) => { setSearch(e.target.value); setUserId(e.target.value); }} placeholder="Search by email, or type an id" />
                        {found.length > 0 && (
                          <div className="rt-found">
                            {found.map((u) => <button key={u.id} type="button" onMouseDown={(e) => { e.preventDefault(); setPicked(u); setUserId(u.id); setSearch(""); }}><b>{u.email}</b><small>{u.id}</small></button>)}
                          </div>
                        )}
                      </div>
                    )}
                  </Field>
                  <Field label="Roles" optional hint={picked ? "Empty uses this user's own roles." : undefined}><TagInput value={roles || []} onChange={(v) => setRoles(v.length ? v : null)} placeholder="admin, editor" /></Field>
                  <Field label="Permissions" optional><TagInput value={permissions || []} onChange={(v) => setPermissions(v.length ? v : null)} placeholder="posts.write" /></Field>
                  <div className="row top" style={{ gap: 12 }}>
                    <Field label="Organization" optional className="grow"><input value={org} onChange={(e) => setOrg(e.target.value)} placeholder="acme" /></Field>
                    <Field label="Org role" optional className="grow"><input value={orgRole} onChange={(e) => setOrgRole(e.target.value)} placeholder="admin" /></Field>
                  </div>
                  <Field label="Sign-in strength"><Segmented value={aal} onChange={setAal} options={[["aal1", "Password"], ["aal2", "Second factor"]]} /></Field>
                </>
              )}
            </div>
          </Card>

          {(needsRecord || needsInput) && (
            <Card icon="book" title="The data">
              <div className="stack">
                {needsRecord && (
                  <>
                    <Field label="Record id" optional hint="Loads the real record from the table."><input className="mono" value={recordId} onChange={(e) => setRecordId(e.target.value)} placeholder="01HX…" /></Field>
                    <Field label="Or a record as JSON" optional hint="Used when no id is given, to try a record that does not exist."><textarea rows={4} className="mono" value={recordText} onChange={(e) => setRecordText(e.target.value)} placeholder='{"owner_id": "…"}' spellCheck={false} /></Field>
                  </>
                )}
                {needsInput && <Field label="Submitted data" optional hint="What the request body would contain."><textarea rows={4} className="mono" value={inputText} onChange={(e) => setInputText(e.target.value)} placeholder='{"title": "Hello"}' spellCheck={false} /></Field>}
              </div>
            </Card>
          )}
        </div>

        <div className="rt-result">
          {error && <div className="alert error">{error}</div>}
          {!out && !error && <div className="empty">{busy ? <Spinner /> : "Choose what to test."}</div>}
          {out && (
            <div className={`stack lg ${busy ? "rt-stale" : ""}`}>
              <Verdict out={out} />
              {out.warnings?.map((w) => <div key={w} className="alert warn">{w}</div>)}
              {out.trace && (
                <Card icon="check" title="Why" description="Each check the policy made, and what it found.">
                  <Trace node={out.trace} />
                </Card>
              )}
              {out.preview && <Preview preview={out.preview} plan={out.plan} />}
              {out.caller && (
                <Card icon="users" title="What the rule saw" description={out.caller.source === "user" ? "Loaded from this user." : out.caller.source === "manual" ? "Values you entered." : undefined}>
                  <pre className="code-block rt-json">{JSON.stringify(out.caller.auth, null, 2)}</pre>
                </Card>
              )}
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
}
