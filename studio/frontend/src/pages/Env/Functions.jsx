import { useMemo, useState } from "react";
import Layout, { sectionNav, useSection } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, Json, JsonInput, Loading, PageHead, PageTabs, Sheet, Status, Table, useAction, when } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

// ── a small Python highlighter: enough to read a function comfortably ─────────────────────────────────────────────────
const KEYWORDS = new Set("def class return if elif else for while in not and or is import from as with try except finally raise yield lambda pass break continue async await global nonlocal assert del".split(" "));
const CONSTANTS = new Set(["None", "True", "False", "self", "ctx"]);
const TOKEN = /(#.*$)|("""|'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(@[\w.]+)|(\b\d[\d_.]*\b)|([A-Za-z_]\w*)|(\s+|.)/g;

function highlight(source) {
  const lines = source.split("\n");
  let triple = null;
  return lines.map((text) => {
    const parts = [];
    if (triple) {
      const end = text.indexOf(triple);
      if (end === -1) return [["str", text]];
      parts.push(["str", text.slice(0, end + 3)]);
      text = text.slice(end + 3);
      triple = null;
    }
    TOKEN.lastIndex = 0;
    let match;
    while ((match = TOKEN.exec(text)) !== null) {
      const [token, comment, string, decorator, number, word] = match;
      if (comment) parts.push(["com", token]);
      else if (string) {
        if ((string === '"""' || string === "'''")) {
          const rest = text.slice(match.index + 3);
          const end = rest.indexOf(string);
          if (end === -1) { parts.push(["str", text.slice(match.index)]); triple = string; break; }
          parts.push(["str", text.slice(match.index, match.index + 3 + end + 3)]);
          TOKEN.lastIndex = match.index + 3 + end + 3;
        } else parts.push(["str", token]);
      } else if (decorator) parts.push(["dec", token]);
      else if (number) parts.push(["num", token]);
      else if (word) parts.push([KEYWORDS.has(word) ? "kw" : CONSTANTS.has(word) ? "const" : "id", token]);
      else parts.push(["plain", token]);
    }
    return parts;
  });
}

// ── TypeScript / JavaScript: keywords, strings (incl. templates), comments, numbers ─────────────────────────────────────
const TS_KEYWORDS = new Set("import export from as const let var function return if else for of in while do switch case default break continue try catch finally throw new class extends async await typeof instanceof type interface enum implements public private readonly yield delete void".split(" "));
const TS_CONSTANTS = new Set(["true", "false", "null", "undefined", "this", "ctx"]);
const TS_TOKEN = /(\/\/.*$)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(\b\d[\d_.]*\b)|([A-Za-z_$][\w$]*)|(\s+|.)/g;

function highlightTs(source) {
  return source.split("\n").map((text) => {
    const parts = [];
    TS_TOKEN.lastIndex = 0;
    let match;
    while ((match = TS_TOKEN.exec(text)) !== null) {
      const [token, comment, string, number, word] = match;
      if (comment) parts.push(["com", token]);
      else if (string) parts.push(["str", token]);
      else if (number) parts.push(["num", token]);
      else if (word) parts.push([TS_KEYWORDS.has(word) ? "kw" : TS_CONSTANTS.has(word) ? "const" : "id", token]);
      else parts.push(["plain", token]);
    }
    return parts;
  });
}

const languageOf = (path) => (/\.py$/.test(path) ? "python" : /\.(ts|mts|js|mjs)$/.test(path) ? "typescript" : "text");

function CodeView({ content, line, language = "python" }) {
  const rows = useMemo(
    () => (language === "python" ? highlight(content) : language === "typescript" ? highlightTs(content) : content.split("\n").map((text) => [["plain", text]])),
    [content, language],
  );
  return (
    <div className="codeview [border:1px_solid_var(--line)] rounded-sm bg-[#121119] text-[#e6e3f7] [font:12.5px/1.65_var(--mono)] overflow-hidden max-h-[62vh] p-[10px_0]" role="region" aria-label="Source code">
      {rows.map((parts, index) => (
        <div key={index} className={`codeview-line block whitespace-pre [&.target]:bg-brand-soft p-[0_16px_0_0] [&_i]:inline-block [&_i]:w-12 [&_i]:pr-3.5 [&_i]:text-right [&_i]:text-[var(--faint,_#9a96ad)] [&_i]:not-italic [&_i]:select-none ${line === index + 1 ? "target" : ""}`} ref={line === index + 1 ? (el) => el?.scrollIntoView({ block: "center" }) : undefined}>
          <span className="codeview-n flex-none w-[52px] pr-3.5 text-right text-faint select-none">{index + 1}</span>
          <span className="codeview-t pr-[18px]">{parts.length ? parts.map(([kind, text], i) => <span key={i} className={`tok-${kind}`}>{text}</span>) : " "}</span>
        </div>
      ))}
    </div>
  );
}

function SourceFile({ env, path, line, branch }) {
  const file = useApi(path ? envPath(env, "/function-source/file") : null, { params: { path, branch } });
  if (!path) return <p className="muted text-muted">This function's file is not available.</p>;
  return (
    <Loading state={file}>
      {(data) => (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm">
          <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 spread flex items-center justify-between gap-3"><code>{data.path}</code><span className="hint text-xs font-book text-muted leading-[1.45]">{data.lines} lines · {(data.size / 1024).toFixed(1)} KB</span></div>
          <CodeView content={data.content} line={line} language={languageOf(data.path)} />
        </div>
      )}
    </Loading>
  );
}

function RunDetail({ env, id, onClose }) {
  const run = useApi(envPath(env, `/function-runs/${id}`));
  return (
    <Sheet title="Run" subtitle={id} icon="functions" onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      <Loading state={run}>
        {(r) => (
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
            <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
              <Status value={r.status} />
              <Badge>{r.function}</Badge>
              <Badge>{r.trigger}</Badge>
              {r.duration_ms != null && <Badge>{Math.round(r.duration_ms)} ms</Badge>}
              {r.kind === "http" && <Badge>{r.method} {r.path}</Badge>}
              {r.status_code ? <Badge tone={r.status_code >= 500 ? "red" : undefined}>HTTP {r.status_code}</Badge> : null}
              {r.cold_start ? <Badge tone="blue">cold start</Badge> : null}
              {r.background_ms != null && <Badge>waitUntil {Math.round(r.background_ms)} ms</Badge>}
              {r.fetches ? <Badge>{r.fetches} fetch{r.fetches === 1 ? "" : "es"}</Badge> : null}
              {r.cpu_ms != null && <Badge>CPU {Math.round(r.cpu_ms)} ms</Badge>}
              {r.memory_peak_mb != null && <Badge>memory {Math.round(r.memory_peak_mb)} MB</Badge>}
              {r.limit_hit && <Badge tone="red">stopped: {r.limit_hit} limit</Badge>}
              <span className="hint text-xs font-book text-muted leading-[1.45]">{when(r.created_at)}</span>
            </div>
            {r.error && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error"><pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{r.error}</pre></div>}
            {r.limits && (
              <Field label="Limits of this run">
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">{["timeout", "memory", "cpu", "concurrency", "max_body", "workers"].filter((k) => r.limits[k] != null).map((k) => <Badge key={k} tone={r.limit_hit === k ? "red" : undefined}>{k} {r.limits[k]}{({ timeout: "s", memory: " MB", cpu: "s", max_body: " MB" })[k] || ""}</Badge>)}</div>
              </Field>
            )}
            <Field label="Input"><Json value={r.input ?? null} /></Field>
            <Field label="Output"><Json value={r.output ?? null} /></Field>
            <Field label={`Log (${(r.logs || []).length})`}>
              {(r.logs || []).length === 0 ? <p className="muted text-muted">Nothing was logged.</p> : (
                <div className="logview [border:1px_solid_var(--line)] rounded-sm bg-sunken p-[10px_12px] [font:12.5px/1.6_var(--mono)] max-h-[260px] overflow-auto [&_code]:ml-2.5 [&_code]:text-muted">
                  {r.logs.map((entry, index) => (
                    <div key={index}><b>{entry.message ?? JSON.stringify(entry)}</b>{entry.data && Object.keys(entry.data).length > 0 ? <code>{JSON.stringify(entry.data)}</code> : null}</div>
                  ))}
                </div>
              )}
            </Field>
            <div className="hint text-xs font-book text-muted leading-[1.45]">{r.request_id ? `request ${r.request_id}` : ""} {r.deployment_id ? `· deployment ${r.deployment_id}` : ""}</div>
          </div>
        )}
      </Loading>
    </Sheet>
  );
}

function RunsTable({ env, fn, onOpen, bleed = false }) {
  const [status, setStatus] = useState("");
  const runs = useApi(envPath(env, "/function-runs"), { params: { ...(fn ? { function: fn } : {}), ...(status ? { status } : {}), limit: 100 }, interval: 8000 });
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 180 }} aria-label="Status">
          <option value="">Any status</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
        </select>
        <span className="hint text-xs font-book text-muted leading-[1.45]">Refreshes every few seconds</span>
      </div>
      <Loading state={runs} empty="No runs yet. Invoke a function, or call it over HTTP.">
        {(data) => (
          <Table
            bleed={bleed}
            rows={data.data}
            onRowClick={(row) => onOpen(row.id)}
            columns={[
              { label: "When", render: (r) => when(r.created_at) },
              ...(fn ? [] : [{ label: "Function", render: (r) => <b>{r.function}</b> }]),
              { label: "Request", render: (r) => (r.kind === "http" ? <code>{r.method} {r.path}</code> : <Badge>{r.trigger}</Badge>) },
              { label: "Status", render: (r) => <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">{<Status value={r.status} />}{r.status_code ? <Badge tone={r.status_code >= 500 ? "red" : r.status_code >= 400 ? "amber" : undefined}>{r.status_code}</Badge> : null}{r.cold_start ? <Badge tone="blue">cold</Badge> : null}</span> },
              { label: "Duration", render: (r) => (r.duration_ms != null ? `${Math.round(r.duration_ms)} ms` : "—") },
              { label: "CPU / memory", render: (r) => (r.cpu_ms != null || r.memory_peak_mb != null ? `${r.cpu_ms != null ? `${Math.round(r.cpu_ms)} ms` : "—"} / ${r.memory_peak_mb != null ? `${Math.round(r.memory_peak_mb)} MB` : "—"}` : "—") },
              { label: "Error", render: (r) => (r.error ? <span className="truncate inline-block max-w-[340px] overflow-hidden text-ellipsis whitespace-nowrap align-bottom" title={r.error}>{String(r.error).split("\n")[0].slice(0, 70)}</span> : "—") },
            ]}
          />
        )}
      </Loading>
    </div>
  );
}

function Invoke({ base, fn }) {
  const [input, setInput] = useState({});
  const [asUser, setAsUser] = useState(null);
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
      {fn.input_fields?.length > 0 && <p className="hint text-xs font-book text-muted leading-[1.45]">Expects: {fn.input_fields.map((f) => `${f.name}${f.required ? "*" : ""} (${f.type})`).join(", ")}</p>}
      <Field label="Input"><JsonInput value={input} onChange={setInput} rows={8} /></Field>
      <Field label="As user (optional)" hint="An auth context; empty invokes with service rights."><JsonInput value={asUser ?? undefined} onChange={setAsUser} rows={3} /></Field>
      <div><Button variant="primary" disabled={busy} onClick={async () => { const r = await run(() => post(`${base}/${fn.name}/invoke`, { input, as_user: asUser || undefined })); if (r) setResult(r); }}>Run now</Button></div>
      {result && <Json value={result} />}
    </div>
  );
}

const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

/** "Try it" for an HTTP function: a real request with a method, a path, headers and a body, as any user. */
function TryHttp({ env, fn }) {
  const allowed = (fn.config?.methods || METHODS).filter((m) => m !== "OPTIONS");
  const [method, setMethod] = useState(allowed.includes("POST") ? "POST" : allowed[0] || "GET");
  const [path, setPath] = useState("/");
  const [headers, setHeaders] = useState({ "content-type": "application/json" });
  const [body, setBody] = useState("{}");
  const [asUser, setAsUser] = useState(null);
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  const send = async () => {
    const r = await run(() => post(envPath(env, `/http-functions/${fn.name}/request`), {
      method, path, headers, body: ["GET", "HEAD"].includes(method) ? null : body, as_user: asUser || undefined,
    }));
    if (r) setResult(r);
  };
  let pretty = result?.body;
  if (result && !result.body_base64) { try { pretty = JSON.stringify(JSON.parse(result.body), null, 2); } catch { /* not JSON */ } }
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
        <select value={method} onChange={(e) => setMethod(e.target.value)} style={{ width: 120 }} aria-label="Method">
          {allowed.map((m) => <option key={m}>{m}</option>)}
        </select>
        <span className="mono font-mono text-ui-sm muted text-muted">/functions/v1/{fn.name}</span>
        <input className="grow flex-1 min-w-0 mono font-mono text-ui-sm" value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path?query=1" aria-label="Path" />
      </div>
      <Field label="Headers"><JsonInput value={headers} onChange={setHeaders} rows={3} /></Field>
      {!["GET", "HEAD"].includes(method) && (
        <Field label="Body"><textarea className="mono font-mono text-ui-sm" rows={7} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
      )}
      <Field label="As user (optional)" hint={'An auth context such as {"authenticated": true, "user_id": "42"}; anonymous when empty. The function\'s own auth setting is not applied here.'}>
        <JsonInput value={asUser ?? undefined} onChange={setAsUser} rows={3} />
      </Field>
      <div><Button variant="primary" disabled={busy} onClick={send}><Icon name="play" />Send</Button></div>
      {result && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
          <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
            <Badge tone={result.status >= 500 ? "red" : result.status >= 400 ? "amber" : "green"}>HTTP {result.status}</Badge>
            <Badge>{Math.round(result.duration_ms)} ms</Badge>
            {result.cold_start && <Badge tone="blue">cold start</Badge>}
            {result.truncated && <Badge tone="amber">first 1 MB shown</Badge>}
          </div>
          <Field label="Response headers">
            <div className="logview [border:1px_solid_var(--line)] rounded-sm bg-sunken p-[10px_12px] [font:12.5px/1.6_var(--mono)] max-h-[260px] overflow-auto [&_code]:ml-2.5 [&_code]:text-muted">{result.headers.map(([k, v], i) => <div key={i}><b>{k}</b> <code>{v}</code></div>)}</div>
          </Field>
          <Field label={result.body_base64 ? "Body (base64: not text)" : "Body"}><pre className="codeview [border:1px_solid_var(--line)] rounded-sm bg-[#121119] text-[#e6e3f7] [font:12.5px/1.65_var(--mono)] overflow-hidden max-h-[62vh] p-[10px_0]" style={{ whiteSpace: "pre-wrap", maxHeight: 360, overflow: "auto" }}>{pretty || <span className="muted text-muted">empty</span>}</pre></Field>
        </div>
      )}
    </div>
  );
}

/** What an HTTP function did lately, from its runs, and the workers running it now. */
function HttpStats({ env, fn }) {
  const detail = useApi(envPath(env, `/http-functions/${fn.name}`), { interval: 10000 });
  const runtime = useApi(envPath(env, "/functions-runtime"), { interval: 10000 });
  const mine = (runtime.data?.http?.functions || []).find((f) => f.function === fn.name);
  const [run, busy] = useAction();
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <Loading state={detail}>
        {(d) => (
          <Card icon="usage" title={`Last ${d.stats.hours} hours`}>
            <dl className="fn-facts m-0 grid gap-0 [&_>_div]:flex [&_>_div]:gap-5 [&_>_div]:p-[10px_0] [&_>_div]:[border-bottom:1px_solid_var(--line)] [&_>_div]:text-body [&_dt]:w-[120px] [&_dt]:text-muted [&_dt]:flex-none [&_dd]:m-0">
              <div><dt>Requests</dt><dd>{d.stats.requests}</dd></div>
              <div><dt>Failed</dt><dd>{d.stats.failed ? <Badge tone="red">{d.stats.failed}</Badge> : 0}</dd></div>
              <div><dt>Latency p50 / p95 / p99</dt><dd>{["p50", "p95", "p99"].map((k) => (d.stats.latency_ms[k] != null ? `${Math.round(d.stats.latency_ms[k])} ms` : "—")).join(" / ")}</dd></div>
              <div><dt>Cold starts</dt><dd>{d.stats.cold_starts}</dd></div>
              <div><dt>Status codes</dt><dd className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">{Object.entries(d.stats.status_codes).map(([k, v]) => <Badge key={k} tone={k === "5xx" ? "red" : k === "4xx" ? "amber" : undefined}>{k}: {v}</Badge>)}</dd></div>
            </dl>
          </Card>
        )}
      </Loading>
      <Card
        flush
        title="Workers"
        actions={<Button disabled={busy} onClick={async () => { if (await run(() => post(envPath(env, "/functions-runtime/restart"), { function: fn.name }), "Workers draining")) runtime.reload(); }}>Restart</Button>}
      >
        <Loading state={runtime}>
          {(rt) => (
            <>
              <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ padding: "8px 16px" }}>Runtime: {rt.mode === "service" ? <>service at <code>{rt.url}</code>{rt.reachable === false ? <Badge tone="red">unreachable</Badge> : null}</> : "inside the server"} · confinement {rt.confinement} · network {rt.network}</p>
              <Table
                rows={mine?.workers || []}
                empty={`No worker is running ${fn.name} right now; the next request starts one${fn.config?.warm ? " (warm workers are being started)" : ""}.`}
                columns={[
                  { label: "Worker", render: (w) => <code>{w.id}</code> },
                  { label: "State", render: (w) => (w.draining ? <Badge tone="amber">draining</Badge> : w.alive ? <Badge tone="green">serving</Badge> : <Badge tone="red">stopped</Badge>) },
                  { label: "In flight", render: (w) => `${w.active} / ${w.concurrency}` },
                  { label: "Served", render: (w) => `${w.served}${w.failed ? ` (${w.failed} failed)` : ""}` },
                  { label: "Heap", render: (w) => (w.heap_bytes ? `${Math.round(w.heap_bytes / 1048576)} / ${w.memory_mb} MB` : "—") },
                  { label: "Idle", render: (w) => `${Math.round(w.idle_seconds)}s` },
                ]}
              />
            </>
          )}
        </Loading>
      </Card>
    </div>
  );
}

const LIMITS = [
  { key: "timeout", label: "Timeout", unit: "s", hint: "Wall-clock time a run may take." },
  { key: "memory", label: "Memory", unit: "MB", hint: "What the run's worker may hold." },
  { key: "cpu", label: "CPU", unit: "s", hint: "CPU time per run. HTTP functions: the longest it may compute without yielding." },
  { key: "concurrency", label: "Runs at once", unit: "", hint: "Runs of this function at once in this environment." },
  { key: "max_body", label: "Request body", unit: "MB", hint: "HTTP functions only." },
  { key: "workers", label: "Workers", unit: "", hint: "Workers this HTTP function may run at once." },
];
const SOURCE_LABEL = { project: "project", default: "installation default", code: "shorter timeout in code", installation: "installation maximum" };

/** What every run of the function may use. Read-only: limits are the project's, set by whoever operates the installation. */
function FunctionLimits({ env, fn }) {
  const all = useApi(envPath(env, "/function-limits"));
  return (
    <Loading state={all}>
      {(data) => {
        const row = data.data.find((r) => r.function === fn.name);
        if (!row) return <p className="muted text-muted">This function is not loaded.</p>;
        const shown = LIMITS.filter((l) => row.kind === "http" || !["max_body", "workers"].includes(l.key));
        return (
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
            <p className="hint text-xs font-book text-muted leading-[1.45]">Every run of every function is held to these limits, so no project can starve another. They are this project's limits, set when it was created and changed by whoever operates this installation; they cannot be changed here.</p>
            {!data.enforced.cpu_and_memory && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)]">This server cannot measure CPU and memory (it is not Linux): only the timeout and runs at once are enforced here.</div>}
            <Card icon="jobs" flush title="Every run gets">
              <Table
                rows={shown}
                columns={[
                  { label: "Limit", render: (l) => <div><b>{l.label}</b><div className="hint text-xs font-book text-muted leading-[1.45]">{l.hint}</div></div> },
                  { label: "Value", render: (l) => <b>{l.key === "concurrency" && row.limits[l.key] === 0 ? "no limit of its own" : `${row.limits[l.key]}${l.unit ? ` ${l.unit}` : ""}`}</b> },
                  { label: "Set by", render: (l) => <Badge tone={row.limits.sources[l.key] === "project" ? "blue" : undefined}>{SOURCE_LABEL[row.limits.sources[l.key]] || row.limits.sources[l.key]}</Badge> },
                ]}
              />
            </Card>
            {row.running && <p className="hint text-xs font-book text-muted leading-[1.45]">Running now: {row.running.now} of {row.running.limit} allowed at once.</p>}
          </div>
        );
      }}
    </Loading>
  );
}

/** One function in the page: a header with "Try it" on the right, then tabs for what it is, what it did lately, and its code. */
function FunctionPanel({ env, fn, base, branch, stats, onRun }) {
  const [tab, setTab] = useState("overview");
  const [trying, setTrying] = useState(false);
  return (
    <div>
      <div className="fn-head flex items-start gap-4 mb-1.5 [&_.grow_h1]:m-0 [&_.grow_h1]:text-[20px] [&_.grow_p]:m-[4px_0_0] [&_.grow_p]:text-muted [&_.grow_p]:text-ui">
        <span className="tile-icon text-sky" style={{ display: "grid", placeItems: "center" }}><Icon name="functions" size={28} /></span>
        <div className="grow flex-1 min-w-0">
          <h1 className="mono font-mono text-ui-sm">{fn.name}</h1>
          <p>{fn.description || fn.module || <code>{fn.kind === "http" ? `ANY /functions/v1/${fn.name}/*` : `POST /functions/v1/${fn.name}`}</code>}</p>
        </div>
        <Button variant="primary" onClick={() => setTrying(true)}><Icon name="play" />Try it</Button>
      </div>
      <PageTabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "overview", label: "Overview" },
          { value: "runs", label: "Recent runs", count: stats?.total || undefined },
          ...(fn.kind === "http" ? [{ value: "runtime", label: "Performance" }] : []),
          { value: "limits", label: "Limits" },
          { value: "code", label: "Code" },
        ]}
      />
      {tab === "overview" && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
          <Card>
            <dl className="fn-facts m-0 grid gap-0 [&_>_div]:flex [&_>_div]:gap-5 [&_>_div]:p-[10px_0] [&_>_div]:[border-bottom:1px_solid_var(--line)] [&_>_div]:text-body [&_dt]:w-[120px] [&_dt]:text-muted [&_dt]:flex-none [&_dd]:m-0">
              {fn.kind === "http" ? (
                <>
                  <div><dt>Call it</dt><dd><code>{(fn.config?.methods || []).filter((m) => m !== "OPTIONS").join(" ")} {fn.url}</code></dd></div>
                  {fn.public_url && <div><dt>Without a key</dt><dd><code>{fn.public_url}</code></dd></div>}
                  <div><dt>Who may</dt><dd><Badge tone={fn.config?.auth === "public" ? "amber" : undefined}>{typeof fn.config?.auth === "string" ? fn.config.auth : JSON.stringify(fn.config?.auth)}</Badge></dd></div>
                  <div><dt>Written with</dt><dd>{fn.style === "deno" ? <code>Deno.serve</code> : <code>serve()</code>}</dd></div>
                  <div><dt>Workers</dt><dd>{fn.config?.warm ? `${fn.config.warm} kept warm` : "started on demand"}{fn.config?.concurrency ? ` · ${fn.config.concurrency} requests each` : ""}</dd></div>
                  {fn.config?.cors && <div><dt>Origins</dt><dd><code>{Array.isArray(fn.config.cors.origins) ? fn.config.cors.origins.join(", ") : fn.config.cors.origins}</code></dd></div>}
                </>
              ) : (
                <>
                  <div><dt>Call it</dt><dd><code>POST /functions/v1/{fn.name}</code></dd></div>
                  <div><dt>Who may</dt><dd><code>{JSON.stringify(fn.policy ?? null)}</code></dd></div>
                </>
              )}
              <div><dt>Timeout</dt><dd>{fn.timeout ? `${fn.timeout}s` : "default"}</dd></div>
              <div><dt>Defined in</dt><dd>{fn.file ? <code>{fn.file}{fn.line ? `:${fn.line}` : ""}</code> : <span className="muted text-muted">unknown</span>}</dd></div>
              <div><dt>Served from</dt><dd><Badge>{fn.source}</Badge></dd></div>
              <div><dt>Recent runs</dt><dd>{stats ? <>{stats.total}{stats.failed ? <> · <Badge tone="red">{stats.failed} failed</Badge></> : null}</> : <span className="muted text-muted">none</span>}</dd></div>
              {fn.tags?.length > 0 && <div><dt>Tags</dt><dd className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">{fn.tags.map((t) => <Badge key={t}>{t}</Badge>)}</dd></div>}
            </dl>
          </Card>
          {fn.input_fields?.length > 0 && (
            <Card icon="resources" flush title="Input fields">
              <Table rows={fn.input_fields} columns={[{ label: "Name", render: (f) => <b>{f.name}</b> }, { label: "Type", key: "type" }, { label: "Required", render: (f) => (f.required ? "Yes" : "—") }, { label: "Limits", render: (f) => [f.max_length && `max ${f.max_length}`, f.minimum != null && `min ${f.minimum}`, f.maximum != null && `max ${f.maximum}`].filter(Boolean).join(", ") || "—" }]} />
            </Card>
          )}
        </div>
      )}
      {tab === "runs" && <Card><RunsTable env={env} fn={fn.name} onOpen={onRun} /></Card>}
      {tab === "runtime" && fn.kind === "http" && <HttpStats env={env} fn={fn} />}
      {tab === "limits" && <FunctionLimits env={env} fn={fn} />}
      {tab === "code" && <Card><SourceFile env={env} path={fn.file} line={fn.line} branch={branch} /></Card>}
      {trying && (
        <Sheet title={`Try ${fn.name}`} subtitle={fn.kind === "http" ? "Send it a request, as any user" : "Call the function now, with any input and identity"} icon="play" tone="sky" onClose={() => setTrying(false)} footer={<Button onClick={() => setTrying(false)}>Close</Button>}>
          {fn.kind === "http" ? <TryHttp env={env} fn={fn} /> : <Invoke base={base} fn={fn} />}
        </Sheet>
      )}
    </div>
  );
}

function Deployments({ env, onChanged }) {
  const rows = useApi(envPath(env, "/function-deployments"), { params: { limit: 50 } });
  const [run, busy] = useAction();
  return (
    <Loading state={rows} empty="Nothing has been deployed with the CLI yet. Run pawabase deploy.">
      {(data) => (
        <Table
          rows={data.data}
          columns={[
            { label: "Deployed", render: (d) => when(d.created_at) },
            { label: "Id", render: (d) => <code>{String(d.id).slice(-10)}</code> },
            { label: "Branch", render: (d) => <Badge>{d.branch}</Badge> },
            { label: "Status", render: (d) => <Status value={d.status} /> },
            { label: "Functions", render: (d) => (d.manifest?.functions ? d.manifest.functions.length : "—") },
            { label: "By", render: (d) => d.created_by || "—" },
            { label: "", render: (d) => (d.status === "superseded" ? <Button size="sm" disabled={busy} onClick={async () => { if (await run(() => post(envPath(env, `/function-deployments/${d.id}/activate`)), "Rolled back to this deployment")) { rows.reload(); onChanged(); } }}>Activate</Button> : null) },
          ]}
        />
      )}
    </Loading>
  );
}

function Source({ env, branch }) {
  const tree = useApi(envPath(env, "/function-source"), { params: { branch } });
  const [chosen, setChosen] = useState(null);
  return (
    <Loading state={tree} empty="">
      {(data) => {
        const path = chosen || data.files.find((f) => f.path.endsWith(".py"))?.path;
        return (
          <div className="source-grid grid grid-cols-[260px_minmax(0,_1fr)] gap-5">
            <aside className="source-files flex flex-col gap-0.5 [border-right:1px_solid_var(--line)] pr-2 max-h-[66vh] overflow-auto [&_button]:text-left [&_button]:bg-none [&_button]:border-0 [&_button]:p-[7px_12px] [&_button]:rounded-sm [&_button]:[font:12.5px_var(--mono)] [&_button]:text-muted [&_button]:cursor-pointer [&_button]:break-all [&_button:hover]:bg-sunken [&_button:hover]:text-text [&_button.on]:bg-brand-soft [&_button.on]:text-brand-strong">
              <div className="hint text-xs font-book text-muted leading-[1.45]" style={{ padding: "0 12px 8px" }}>{data.mounted ? "Mounted code" : `Deployment ${String(data.deployment_id || "").slice(-10)}`}</div>
              {data.files.length === 0 && <p className="muted text-muted" style={{ padding: "0 12px" }}>No files.</p>}
              {data.files.map((f) => (
                <button key={f.path} type="button" className={f.path === path ? "on" : ""} onClick={() => setChosen(f.path)}>{f.path}</button>
              ))}
            </aside>
            <div className="source-view">{path ? <SourceFile env={env} path={path} branch={branch} /> : <p className="muted text-muted">Nothing to show.</p>}</div>
          </div>
        );
      }}
    </Loading>
  );
}

// ── the all-functions list: one row per function, readable at a glance ────────────────────────────────────────────────
const ACCESS_LABEL = {
  public: ["Public", "amber"],
  anyone: ["Anyone", "amber"],
  authenticated: ["Signed in", "blue"],
  in_org: ["Org members", "blue"],
  service: ["Service", ""],
  admin: ["Admins", "brand"],
  owner: ["Owner", "brand"],
};

function Access({ policy }) {
  if (policy == null || policy === "") return <span className="fn-dash">—</span>;
  const known = typeof policy === "string" ? ACCESS_LABEL[policy] : null;
  if (known) return <Badge tone={known[1]}>{known[0]}</Badge>;
  const text = typeof policy === "string" ? policy : JSON.stringify(policy);
  return <code className="fn-code" title={text}>{text}</code>;
}

function FilePath({ path, line }) {
  if (!path) return <span className="fn-dash">—</span>;
  const cut = path.lastIndexOf("/");
  return (
    <span className="fn-file" title={`${path}${line ? `:${line}` : ""}`}>
      {cut === -1 ? null : <span className="fn-dir">{path.slice(0, cut + 1)}</span>}
      {cut === -1 ? path : path.slice(cut + 1)}
      {line ? <span className="fn-dir">:{line}</span> : null}
    </span>
  );
}

function FunctionName({ fn }) {
  const http = fn.kind === "http";
  return (
    <div className="fn-name">
      <span className={`fn-tile ${http ? "http" : "call"}`} aria-hidden="true"><Icon name={http ? "braces" : "functions"} size={16} /></span>
      <div className="fn-name-text">
        <div className="fn-name-row">
          <b className="fn-title">{fn.name}</b>
          {http ? <Badge tone="blue">HTTP</Badge> : null}
          {(fn.tags || []).slice(0, 2).map((tag) => <Badge key={tag} tone="brand">{tag}</Badge>)}
        </div>
        <div className="fn-desc" title={fn.description || undefined}>{fn.description || <span className="fn-dash">No description</span>}</div>
      </div>
    </div>
  );
}

function Activity({ buckets }) {
  return (
    <span className="fn-spark" aria-hidden="true">
      {(buckets || Array.from({ length: 12 }, () => ({ n: 0, failed: 0 }))).map((b, i) => (
        <i key={i} className={b.failed ? "bad" : b.n ? "on" : ""} style={{ height: b.n ? Math.min(14, 4 + b.n * 3) : 2 }} />
      ))}
    </span>
  );
}

const FUNCTION_SECTIONS = ["all", "runs", "deployments", "source"];

export default function Functions({ env }) {
  const base = envPath(env, "/functions");
  const list = useApi(base);
  const runs = useApi(envPath(env, "/function-runs"), { params: { limit: 200 } });
  const [section, setSection] = useSection((value) => FUNCTION_SECTIONS.includes(value) || value.startsWith("fn:"), "all");
  const [openRun, setOpenRun] = useState(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [run, busy] = useAction();

  const stats = useMemo(() => {
    const out = {};
    for (const r of runs.data?.data || []) {
      const s = (out[r.function] ||= { total: 0, failed: 0, last: r.created_at });
      s.total += 1;
      if (r.status === "failed") s.failed += 1;
    }
    return out;
  }, [runs.data]);

  const activity = useMemo(() => {
    const out = {};
    const now = Date.now();
    for (const r of runs.data?.data || []) {
      const hours = Math.floor((now - new Date(r.created_at).getTime()) / 3600000);
      if (hours < 0 || hours > 11) continue;
      const a = (out[r.function] ||= Array.from({ length: 12 }, () => ({ n: 0, failed: 0 })));
      const bucket = a[11 - hours];
      bucket.n += 1;
      if (r.status === "failed") bucket.failed += 1;
    }
    return out;
  }, [runs.data]);

  const functions = list.data?.data || [];
  const needle = query.trim().toLowerCase();
  const shown = functions.filter((f) => !needle || `${f.name} ${f.description || ""} ${f.file || ""}`.toLowerCase().includes(needle));
  const selected = section.startsWith("fn:") ? functions.find((f) => f.name === section.slice(3)) : null;
  const reloadAll = () => { list.reload(); runs.reload(); };

  const subnav = sectionNav("Functions", [
    { items: [["all", "All functions", null, { icon: "functions" }]] },
    { title: "Functions", items: shown.map((f) => [`fn:${f.name}`, f.name, f.file || undefined, {
      mono: true,
      icon: "functions",
      tag: stats[f.name]?.total ? String(stats[f.name].total) : f.kind === "http" ? "HTTP" : undefined,
      flag: stats[f.name]?.failed ? "critical" : undefined,
      title: f.description || f.name,
    }]) },
    { title: "Activity", pinned: true, items: [["runs", "Runs", null, { icon: "jobs" }], ["deployments", "Deployments", null, { icon: "upload" }], ["source", "Source files", null, { icon: "code" }]] },
  ], section, setSection, {
    loading: list.loading && !list.data,
    search: { value: query, onChange: setQuery, placeholder: "Find a function" },
  });

  return (
    <Layout title="Functions" subnav={subnav}>
      {section === "all" && (
        <>
          <PageHead
            title="Functions"
            description={<>HTTP functions in <code>functions/&lt;name&gt;/index.ts</code> answer any request at <code>/functions/v1/&lt;name&gt;/…</code>; <code>fn()</code> and <code>@function</code> functions take JSON at <code>POST /functions/v1/&lt;name&gt;</code> and are called from routes, flows, schedules and events too.</>}
            actions={<Button disabled={busy} onClick={async () => { if (await run(() => post(`/code/reload`), "Code reloaded")) reloadAll(); }}>Reload code</Button>}
          />
          <Loading state={list}>
            {(data) => {
              const visible = kind === "all" ? shown : shown.filter((f) => (kind === "http" ? f.kind === "http" : f.kind !== "http"));
              const failing = shown.filter((f) => stats[f.name]?.failed).length;
              const count = (wanted) => shown.filter((f) => (wanted === "http" ? f.kind === "http" : f.kind !== "http")).length;
              return (
                <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
                  {data.errors?.length > 0 && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error"><b>Code failed to load</b><pre>{data.errors.join("\n")}</pre></div>}
                  <Card bleed
                    flush
                    title="All functions"
                    description={`${shown.length} loaded${failing ? ` · ${failing} with failures` : ""}`}
                    actions={
                      <div className="chips">
                        {[["all", "All", shown.length], ["call", "fn()", count("call")], ["http", "HTTP", count("http")]].map(([value, label, n]) => (
                          <button key={value} type="button" className={`chip ${kind === value ? "on" : ""}`} aria-pressed={kind === value} onClick={() => setKind(value)}>
                            {label}<span className="fn-chip-n">{n}</span>
                          </button>
                        ))}
                      </div>
                    }
                  >
                    <div className="fn-table">
                      <Table
                        rows={visible}
                        empty="No functions loaded. Add a module under functions/ and deploy, or reload."
                        onRowClick={(f) => setSection(`fn:${f.name}`)}
                        columns={[
                          { label: "Function", render: (f) => <FunctionName fn={f} /> },
                          { label: "Access", render: (f) => <Access policy={f.policy} /> },
                          { label: "Source", render: (f) => <FilePath path={f.file} line={f.line} /> },
                          { label: "Timeout", render: (f) => (f.timeout ? <span className="fn-time">{Math.round(f.timeout)}<span className="fn-unit">s</span></span> : <span className="fn-dash">—</span>) },
                          { label: "Recent runs", render: (f) => { const s = stats[f.name]; if (!s) return <span className="fn-dash">—</span>; return <span className="fn-runs"><Activity buckets={activity[f.name]} /><b className={`fn-count${s.total ? "" : " zero"}`}>{s.total}</b>{s.failed ? <Badge tone="red">{s.failed}</Badge> : null}</span>; } },
                        ]}
                      />
                    </div>
                  </Card>
                  <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">{(data.modules || []).map((m) => <Badge key={m}>{m}</Badge>)}{data.router && <Badge tone="blue">routes.py router</Badge>}</div>
                </div>
              );
            }}
          </Loading>
        </>
      )}
      {selected && <FunctionPanel key={selected.name} env={env} fn={selected} base={base} branch="main" stats={stats[selected.name]} onRun={setOpenRun} />}
      {section.startsWith("fn:") && !selected && list.data && <PageHead title="Function not found" description={`No function named ${section.slice(3)} is loaded in this environment.`} />}
      {section === "runs" && <><PageHead title="Runs" description="Every function call, newest first." /><RunsTable env={env} onOpen={setOpenRun} bleed /></>}
      {section === "deployments" && <><PageHead title="Deployments" description="What pawabase deploy shipped. Activate an older one to roll back." /><Card bleed icon="releases" flush><Deployments env={env} onChanged={reloadAll} /></Card></>}
      {section === "source" && <><PageHead title="Source files" description="The code the running deployment was built from." /><Card icon="code"><Source env={env} branch="main" /></Card></>}

      {openRun && <RunDetail env={env} id={openRun} onClose={() => setOpenRun(null)} />}
    </Layout>
  );
}
