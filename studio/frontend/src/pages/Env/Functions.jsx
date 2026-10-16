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

function CodeView({ content, line, language = "python" }) {
  const rows = useMemo(() => (language === "python" ? highlight(content) : content.split("\n").map((text) => [["plain", text]])), [content, language]);
  return (
    <div className="codeview" role="region" aria-label="Source code">
      {rows.map((parts, index) => (
        <div key={index} className={`codeview-line ${line === index + 1 ? "target" : ""}`} ref={line === index + 1 ? (el) => el?.scrollIntoView({ block: "center" }) : undefined}>
          <span className="codeview-n">{index + 1}</span>
          <span className="codeview-t">{parts.length ? parts.map(([kind, text], i) => <span key={i} className={`tok-${kind}`}>{text}</span>) : " "}</span>
        </div>
      ))}
    </div>
  );
}

function SourceFile({ env, path, line, branch }) {
  const file = useApi(path ? envPath(env, "/function-source/file") : null, { params: { path, branch } });
  if (!path) return <p className="muted">This function's file is not available.</p>;
  return (
    <Loading state={file}>
      {(data) => (
        <div className="stack sm">
          <div className="row spread"><code>{data.path}</code><span className="hint">{data.lines} lines · {(data.size / 1024).toFixed(1)} KB</span></div>
          <CodeView content={data.content} line={line} language={data.path.endsWith(".py") ? "python" : "text"} />
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
          <div className="stack lg">
            <div className="row wrap">
              <Status value={r.status} />
              <Badge>{r.function}</Badge>
              <Badge>{r.trigger}</Badge>
              {r.duration_ms != null && <Badge>{Math.round(r.duration_ms)} ms</Badge>}
              <span className="hint">{when(r.created_at)}</span>
            </div>
            {r.error && <div className="alert error"><pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{r.error}</pre></div>}
            <Field label="Input"><Json value={r.input ?? null} /></Field>
            <Field label="Output"><Json value={r.output ?? null} /></Field>
            <Field label={`Log (${(r.logs || []).length})`}>
              {(r.logs || []).length === 0 ? <p className="muted">Nothing was logged.</p> : (
                <div className="logview">
                  {r.logs.map((entry, index) => (
                    <div key={index}><b>{entry.message ?? JSON.stringify(entry)}</b>{entry.data && Object.keys(entry.data).length > 0 ? <code>{JSON.stringify(entry.data)}</code> : null}</div>
                  ))}
                </div>
              )}
            </Field>
            <div className="hint">{r.request_id ? `request ${r.request_id}` : ""} {r.deployment_id ? `· deployment ${r.deployment_id}` : ""}</div>
          </div>
        )}
      </Loading>
    </Sheet>
  );
}

function RunsTable({ env, fn, onOpen }) {
  const [status, setStatus] = useState("");
  const runs = useApi(envPath(env, "/function-runs"), { params: { ...(fn ? { function: fn } : {}), ...(status ? { status } : {}), limit: 100 }, interval: 8000 });
  return (
    <div className="stack">
      <div className="row">
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 180 }} aria-label="Status">
          <option value="">Any status</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
        </select>
        <span className="hint">Refreshes every few seconds</span>
      </div>
      <Loading state={runs} empty="No runs yet. Invoke a function, or call it over HTTP.">
        {(data) => (
          <Table
            rows={data.data}
            onRowClick={(row) => onOpen(row.id)}
            columns={[
              { label: "When", render: (r) => when(r.created_at) },
              ...(fn ? [] : [{ label: "Function", render: (r) => <b>{r.function}</b> }]),
              { label: "Trigger", render: (r) => <Badge>{r.trigger}</Badge> },
              { label: "Status", render: (r) => <Status value={r.status} /> },
              { label: "Duration", render: (r) => (r.duration_ms != null ? `${Math.round(r.duration_ms)} ms` : "—") },
              { label: "Error", render: (r) => (r.error ? <span className="truncate" title={r.error}>{String(r.error).split("\n")[0].slice(0, 70)}</span> : "—") },
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
    <div className="stack">
      {fn.input_fields?.length > 0 && <p className="hint">Expects: {fn.input_fields.map((f) => `${f.name}${f.required ? "*" : ""} (${f.type})`).join(", ")}</p>}
      <Field label="Input"><JsonInput value={input} onChange={setInput} rows={8} /></Field>
      <Field label="As user (optional)" hint="An auth context; empty invokes with service rights."><JsonInput value={asUser ?? undefined} onChange={setAsUser} rows={3} /></Field>
      <div><Button variant="primary" disabled={busy} onClick={async () => { const r = await run(() => post(`${base}/${fn.name}/invoke`, { input, as_user: asUser || undefined })); if (r) setResult(r); }}>Run now</Button></div>
      {result && <Json value={result} />}
    </div>
  );
}

/** One function in the page: a header with "Try it" on the right, then tabs for what it is, what it did lately, and its code. */
function FunctionPanel({ env, fn, base, branch, stats, onRun }) {
  const [tab, setTab] = useState("overview");
  const [trying, setTrying] = useState(false);
  return (
    <div>
      <div className="fn-head">
        <span className="tile-icon pastel sky" style={{ width: 40, height: 40, display: "grid", placeItems: "center", borderRadius: "var(--r)" }}><Icon name="functions" size={18} /></span>
        <div className="grow">
          <h1 className="mono">{fn.name}</h1>
          <p>{fn.description || fn.module || <code>POST /functions/v1/{fn.name}</code>}</p>
        </div>
        <Button variant="primary" onClick={() => setTrying(true)}><Icon name="play" />Try it</Button>
      </div>
      <PageTabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "overview", label: "Overview" },
          { value: "runs", label: "Recent runs", count: stats?.total || undefined },
          { value: "code", label: "Code" },
        ]}
      />
      {tab === "overview" && (
        <div className="stack lg">
          <Card>
            <dl className="fn-facts">
              <div><dt>Call it</dt><dd><code>POST /functions/v1/{fn.name}</code></dd></div>
              <div><dt>Who may</dt><dd><code>{JSON.stringify(fn.policy ?? null)}</code></dd></div>
              <div><dt>Timeout</dt><dd>{fn.timeout ? `${fn.timeout}s` : "default"}</dd></div>
              <div><dt>Defined in</dt><dd>{fn.file ? <code>{fn.file}{fn.line ? `:${fn.line}` : ""}</code> : <span className="muted">unknown</span>}</dd></div>
              <div><dt>Served from</dt><dd><Badge>{fn.source}</Badge></dd></div>
              <div><dt>Recent runs</dt><dd>{stats ? <>{stats.total}{stats.failed ? <> · <Badge tone="red">{stats.failed} failed</Badge></> : null}</> : <span className="muted">none</span>}</dd></div>
              {fn.tags?.length > 0 && <div><dt>Tags</dt><dd className="row wrap">{fn.tags.map((t) => <Badge key={t}>{t}</Badge>)}</dd></div>}
            </dl>
          </Card>
          {fn.input_fields?.length > 0 && (
            <Card flush title="Input fields">
              <Table rows={fn.input_fields} columns={[{ label: "Name", render: (f) => <b>{f.name}</b> }, { label: "Type", key: "type" }, { label: "Required", render: (f) => (f.required ? "Yes" : "—") }, { label: "Limits", render: (f) => [f.max_length && `max ${f.max_length}`, f.minimum != null && `min ${f.minimum}`, f.maximum != null && `max ${f.maximum}`].filter(Boolean).join(", ") || "—" }]} />
            </Card>
          )}
        </div>
      )}
      {tab === "runs" && <Card><RunsTable env={env} fn={fn.name} onOpen={onRun} /></Card>}
      {tab === "code" && <Card><SourceFile env={env} path={fn.file} line={fn.line} branch={branch} /></Card>}
      {trying && (
        <Sheet title={`Try ${fn.name}`} subtitle="Call the function now, with any input and identity" icon="play" tone="sky" onClose={() => setTrying(false)} footer={<Button onClick={() => setTrying(false)}>Close</Button>}>
          <Invoke base={base} fn={fn} />
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
          <div className="source-grid">
            <aside className="source-files">
              <div className="hint" style={{ padding: "0 12px 8px" }}>{data.mounted ? "Mounted code" : `Deployment ${String(data.deployment_id || "").slice(-10)}`}</div>
              {data.files.length === 0 && <p className="muted" style={{ padding: "0 12px" }}>No files.</p>}
              {data.files.map((f) => (
                <button key={f.path} type="button" className={f.path === path ? "on" : ""} onClick={() => setChosen(f.path)}>{f.path}</button>
              ))}
            </aside>
            <div className="source-view">{path ? <SourceFile env={env} path={path} branch={branch} /> : <p className="muted">Nothing to show.</p>}</div>
          </div>
        );
      }}
    </Loading>
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

  const functions = list.data?.data || [];
  const needle = query.trim().toLowerCase();
  const shown = functions.filter((f) => !needle || `${f.name} ${f.description || ""} ${f.file || ""}`.toLowerCase().includes(needle));
  const selected = section.startsWith("fn:") ? functions.find((f) => f.name === section.slice(3)) : null;
  const reloadAll = () => { list.reload(); runs.reload(); };

  const subnav = sectionNav("Functions", [
    { items: [["all", "All functions", list.data ? `${functions.length} loaded` : undefined]] },
    { title: "Functions", items: shown.map((f) => [`fn:${f.name}`, f.name, f.file || undefined, {
      mono: true,
      tag: stats[f.name]?.total ? String(stats[f.name].total) : undefined,
      flag: stats[f.name]?.failed ? "critical" : undefined,
      title: f.description || f.name,
    }]) },
    { title: "Activity", items: [["runs", "Runs", "Every call, newest first"], ["deployments", "Deployments", "What the CLI shipped"], ["source", "Source files"]] },
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
            description={<>Python in <code>functions/*.py</code>, registered with <code>@function</code>. Call them over HTTP at <code>/functions/v1/&lt;name&gt;</code>, or from routes, flows, schedules and events.</>}
            actions={<Button disabled={busy} onClick={async () => { if (await run(() => post(`/code/reload`), "Code reloaded")) reloadAll(); }}>Reload code</Button>}
          />
          <Loading state={list}>
            {(data) => (
              <div className="stack lg">
                {data.errors?.length > 0 && <div className="alert error"><b>Code failed to load</b><pre>{data.errors.join("\n")}</pre></div>}
                <Card flush>
                  <Table
                    rows={shown}
                    empty="No functions loaded. Add a module under functions/ and deploy, or reload."
                    onRowClick={(f) => setSection(`fn:${f.name}`)}
                    columns={[
                      { label: "Name", render: (f) => <div><b>{f.name}</b>{f.description ? <div className="hint">{f.description}</div> : null}</div> },
                      { label: "File", render: (f) => (f.file ? <code>{f.file}{f.line ? `:${f.line}` : ""}</code> : "—") },
                      { label: "Policy", render: (f) => <code>{typeof f.policy === "string" ? f.policy : JSON.stringify(f.policy ?? null)}</code> },
                      { label: "Timeout", render: (f) => (f.timeout ? `${f.timeout}s` : "—") },
                      { label: "Recent runs", render: (f) => { const s = stats[f.name]; return s ? <span>{s.total}{s.failed ? <Badge tone="red">{s.failed} failed</Badge> : null}</span> : <span className="muted">none</span>; } },
                    ]}
                  />
                </Card>
                <div className="row wrap">{(data.modules || []).map((m) => <Badge key={m}>{m}</Badge>)}{data.router && <Badge tone="blue">routes.py router</Badge>}</div>
              </div>
            )}
          </Loading>
        </>
      )}
      {selected && <FunctionPanel key={selected.name} env={env} fn={selected} base={base} branch="main" stats={stats[selected.name]} onRun={setOpenRun} />}
      {section.startsWith("fn:") && !selected && list.data && <PageHead title="Function not found" description={`No function named ${section.slice(3)} is loaded in this environment.`} />}
      {section === "runs" && <><PageHead title="Runs" description="Every function call, newest first." /><Card><RunsTable env={env} onOpen={setOpenRun} /></Card></>}
      {section === "deployments" && <><PageHead title="Deployments" description="What pawabase deploy shipped. Activate an older one to roll back." /><Card flush><Deployments env={env} onChanged={reloadAll} /></Card></>}
      {section === "source" && <><PageHead title="Source files" description="The code the running deployment was built from." /><Card><Source env={env} branch="main" /></Card></>}

      {openRun && <RunDetail env={env} id={openRun} onClose={() => setOpenRun(null)} />}
    </Layout>
  );
}
