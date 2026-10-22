// Rules tester: can this person pass this check? Pick who, pick what to check, read the answer.
// Everything is chosen from a list; nothing has to be typed. The answer comes with its reasons in plain words.

import { useEffect, useMemo, useRef, useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Spinner } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";
import "./rules/rules.css";

const NOT_SIGNED_IN = "anonymous";
const SERVICE = "service";
const BUILT_IN = [
  ["public", "Anyone, signed in or not"],
  ["authenticated", "Any signed-in user"],
  ["owner", "The owner of the record"],
  ["mfa", "Signed in with a second factor"],
  ["service", "Secret keys only"],
  ["deny", "No one"],
];
const ACTIONS = { list: "see the list", get: "open one", create: "create", update: "change", delete: "delete" };

// ── plain-language reasons ──────────────────────────────────────────

const NAMES = { "$auth.authenticated": "being signed in", "$credential.is_service": "using a secret key", "$auth.user_id": "their user id", "$auth.email": "their email", "$auth.roles": "their roles", "$auth.org": "their organization", "$auth.org_role": "their role in the organization", "$auth.aal": "their sign-in strength", "$auth.permissions": "their permissions" };
const WORDS = { eq: "is", ne: "is not", gt: "is more than", gte: "is at least", lt: "is less than", lte: "is at most", in: "is one of", not_in: "is not one of", contains: "includes", starts_with: "starts with", ends_with: "ends with", matches: "matches" };
const UNARY = { exists: ["is set", "isn't set"], truthy: ["is set", "isn't set"], empty: ["is empty", "isn't empty"] };
// Checks on who the caller is, said the way a person would.
const WHOLE = {
  "$auth.org": ["They're in an organization", "They're not in an organization"],
  "$auth.org_role": ["They have a role in an organization", "They have no role in an organization"],
  "$auth.user_id": ["They are signed in", "They are not signed in"],
  "$auth.email": ["They have an email address", "They have no email address"],
};

const value = (v) => (v === null || v === undefined ? "nothing" : Array.isArray(v) ? (v.length ? v.join(", ") : "none") : typeof v === "object" ? JSON.stringify(v) : String(v));
const nameOf = (o) => (o.expr ? NAMES[o.expr] || (o.expr.startsWith("$record.") ? `the row's ${o.expr.slice(8)}` : o.expr.startsWith("$input.") ? `the submitted ${o.expr.slice(7)}` : o.expr.replace(/^\$/, "")) : null);
const operand = (o) => (nameOf(o) ? `${nameOf(o)} (${value(o.value)})` : value(o.value));
const list = (arg) => (Array.isArray(arg) ? arg.join(" or ") : String(arg));

/** One check as a sentence, and whether it held. */
function sentence(node) {
  const ok = node.result === true;
  switch (node.op) {
    case "literal": return node.result ? "Everyone is allowed" : "No one is allowed";
    case "service": return "Secret keys and Studio can always do this";
    case "python": return "This check is written in code, so it can't be explained here";
    case "authenticated": return ok ? "They are signed in" : "They are not signed in";
    case "role": return `${ok ? "They have" : "They don't have"} the role ${list(node.arg)}`;
    case "permission": return `${ok ? "They have" : "They don't have"} the permission ${list(node.arg)}`;
    case "owner": return ok ? "They own this record" : "They don't own this record";
    case "aal": return ok ? "They signed in with a second factor" : "They did not sign in with a second factor";
    case "kind": case "service_only": return `${ok ? "They are" : "They are not"} ${node.arg}`;
    default:
      if (WORDS[node.op]) return `${operand(node.left)} ${ok ? WORDS[node.op] : `is not (${WORDS[node.op]})`} ${operand(node.right)}`.replace(" is not (is not)", " is");
      if (UNARY[node.op]) {
        const whole = node.op !== "empty" && WHOLE[node.operand.expr];
        return whole ? whole[ok ? 0 : 1] : `${nameOf(node.operand) || value(node.operand.value)} ${UNARY[node.op][ok ? 0 : 1]}`;
      }
      return node.op;
  }
}

/** The few checks that decided it: the ones that failed for a "no", the ones that held for a "yes". */
function reasons(node, want = node.result) {
  if (!node) return [];
  if (node.op === "all") {
    const picked = want ? node.children : node.children.filter((c) => c.result === false);
    return picked.flatMap((c) => reasons(c, want));
  }
  if (node.op === "any") {
    const picked = want ? node.children.filter((c) => c.result === true).slice(0, 1) : node.children;
    return picked.flatMap((c) => reasons(c, want));
  }
  if (node.op === "not") return node.children.flatMap((c) => reasons(c, !want));
  return [{ text: sentence(node), ok: node.result === true }];
}

// ── the technical view, for those who want it ───────────────────────

function Tree({ node }) {
  const tone = node.result === true ? "pass" : node.result === false ? "fail" : "info";
  const title = ["all", "any", "not"].includes(node.op) ? { all: "All of these", any: "Any of these", not: "None of these" }[node.op] : sentence(node);
  return (
    <li className={`rt-node ${tone}`}>
      <div className="rt-row"><span className="rt-mark">{node.result === true ? <Icon name="check" size={12} /> : node.result === false ? <Icon name="x" size={12} /> : "·"}</span><span>{title}</span></div>
      {node.children?.length > 0 && <ul className="rt-children">{node.children.map((c, i) => <Tree key={i} node={c} />)}</ul>}
    </li>
  );
}

// ── the page ─────────────────────────────────────────────────────────

export default function Rules({ env }) {
  const base = envPath(env);
  const users = useApi(`/envs/${env}/users`, { service: "auth", params: { limit: 100 } });
  const policies = useApi(`${base}/policies`);
  const resources = useApi(`${base}/resources`, { params: { limit: 500 } });

  const [who, setWho] = useState(NOT_SIGNED_IN);
  const [what, setWhat] = useState("policy:authenticated");
  const [row, setRow] = useState("");
  const [rows, setRows] = useState([]);
  const [out, setOut] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [details, setDetails] = useState(false);
  const run = useRef(0);

  const people = users.data?.data || [];
  const named = (policies.data?.data || []).map((p) => p.name);
  const tableRules = useMemo(() => (resources.data?.data || []).flatMap((r) => Object.entries(r.operations || {}).filter(([, s]) => s?.enabled).map(([op]) => ({ resource: r.name, op }))), [resources.data]);

  // The question: "policy:<name>" for a named check, "table:<resource>:<action>" for what a table allows.
  const [kind, a, b] = what.split(":");
  const isTable = kind === "table";
  const needsRow = isTable && ["get", "update", "delete"].includes(b);

  // Rows to pick from, for checks that are about one record.
  useEffect(() => {
    setRow("");
    setRows([]);
    if (!needsRow) return;
    get(`${base}/resources/${a}/records`, { params: { per_page: 25 } })
      .then((r) => { const found = r?.data || []; setRows(found); if (found[0]) setRow(String(found[0].id ?? Object.values(found[0])[0])); })
      .catch(() => {});
  }, [what]); // eslint-disable-line react-hooks/exhaustive-deps

  const label = (r) => {
    const text = Object.entries(r).find(([k, v]) => k !== "id" && typeof v === "string" && v && v.length < 40 && !/_at$|_id$/.test(k));
    return text ? `${text[1]}` : `#${String(r.id).slice(-6)}`;
  };

  const person = people.find((p) => p.id === who);
  const caller = who === NOT_SIGNED_IN ? { kind: "anonymous" } : who === SERVICE ? { kind: "service" } : { kind: "user", user_id: who };
  const request = useMemo(() => ({
    ...(isTable ? { resource: a, operation: b } : { policy: a }),
    caller,
    record_id: needsRow && row ? row : null,
  }), [what, who, row]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const mine = ++run.current;
    setBusy(true);
    const t = setTimeout(async () => {
      try {
        const answer = await post(`${base}/policies/test`, request);
        if (mine === run.current) { setOut(answer); setError(null); }
      } catch (e) {
        if (mine === run.current) { setError(e.message); setOut(null); }
      } finally {
        if (mine === run.current) setBusy(false);
      }
    }, 150);
    return () => clearTimeout(t);
  }, [JSON.stringify(request)]); // eslint-disable-line react-hooks/exhaustive-deps

  const subject = who === NOT_SIGNED_IN ? "Someone who isn't signed in" : who === SERVICE ? "A secret key" : person?.email || "This user";
  const yes = out?.allowed;
  const why = out?.trace ? reasons(out.trace).slice(0, 4) : [];
  const disabled = out && out.enabled === false;

  return (
    <Layout title="Rules tester">
      <div className="rt-simple">
        <h1>Can they do it?</h1>
        <p className="rt-lead">Pick a person and something to check. You'll see the answer and why.</p>

        <div className="rt-pickers">
          <label>
            <span>Who</span>
            <select value={who} onChange={(e) => setWho(e.target.value)}>
              <option value={NOT_SIGNED_IN}>Someone not signed in</option>
              <option value={SERVICE}>A secret key (your server)</option>
              {people.length > 0 && <optgroup label="Your users">{people.map((p) => <option key={p.id} value={p.id}>{p.email}</option>)}</optgroup>}
            </select>
          </label>
          <label>
            <span>Check</span>
            <select value={what} onChange={(e) => setWhat(e.target.value)}>
              <optgroup label="Rules">
                {BUILT_IN.map(([n, text]) => <option key={n} value={`policy:${n}`}>{text}</option>)}
                {named.filter((n) => !BUILT_IN.some(([b]) => b === n)).map((n) => <option key={n} value={`policy:${n}`}>{n}</option>)}
              </optgroup>
              {tableRules.length > 0 && (
                <optgroup label="Who can use your tables">
                  {tableRules.map((t) => <option key={`${t.resource}:${t.op}`} value={`table:${t.resource}:${t.op}`}>{t.resource}: {ACTIONS[t.op]}</option>)}
                </optgroup>
              )}
            </select>
          </label>
          {needsRow && rows.length > 0 && (
            <label className="wide">
              <span>Which one</span>
              <select value={row} onChange={(e) => setRow(e.target.value)}>
                {rows.map((r) => <option key={r.id} value={String(r.id)}>{label(r)}</option>)}
              </select>
            </label>
          )}
        </div>

        <div className={`rt-answer ${error ? "bad" : disabled ? "warn" : yes ? "ok" : out ? "bad" : ""} ${busy ? "busy" : ""}`} aria-live="polite">
          {error && <p>{error}</p>}
          {!out && !error && <div className="rt-wait"><Spinner /></div>}
          {out && !error && (
            <>
              <div className="rt-answer-head">
                <span className="rt-answer-icon"><Icon name={yes ? "check" : "x"} size={22} /></span>
                <div>
                  <b>{disabled ? "Not available" : yes ? "Yes" : "No"}</b>
                  <p>{disabled ? out.reason : `${subject} ${yes ? "can" : "can't"} ${isTable ? `${ACTIONS[b]} ${a}` : "pass this check"}.`}</p>
                </div>
              </div>
              {why.length > 0 && !disabled && (
                <ul className="rt-why">
                  {why.map((r, i) => <li key={i} className={r.ok ? "ok" : "bad"}><span><Icon name={r.ok ? "check" : "x"} size={12} /></span>{r.text}</li>)}
                </ul>
              )}
              {out.status === 404 && !yes && <p className="rt-fine">The row exists, but they can't see it, so it looks like it isn't there.</p>}
              {out.warnings?.length > 0 && <p className="rt-fine">{out.warnings[0]}</p>}
            </>
          )}
        </div>

        {out?.trace && !disabled && (
          <div className="rt-more">
            <button type="button" className="linklike" onClick={() => setDetails(!details)}>{details ? "Hide details" : "Show details"}</button>
            {details && <ul className="rt-tree"><Tree node={out.trace} /></ul>}
          </div>
        )}
      </div>
    </Layout>
  );
}
