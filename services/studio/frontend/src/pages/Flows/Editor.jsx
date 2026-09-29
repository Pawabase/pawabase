import { router } from "@inertiajs/react";
import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider,
  addEdge, useEdgesState, useNodesState, useReactFlow,
} from "@xyflow/react";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Field, Json, JsonInput, Tabs, useAction } from "../../components/ui";
import { envPath, post, put } from "../../lib/api";

const CATEGORY_ORDER = ["triggers", "control", "resources", "responses", "auth", "data", "code", "events", "jobs", "mail", "storage", "realtime", "cache", "integrations", "observability"];
const rank = (c) => (CATEGORY_ORDER.includes(c) ? CATEGORY_ORDER.indexOf(c) : 99);
const plain = (text) => (text || "").replace(/``/g, "");
const BlocksContext = createContext({ byKey: {}, trace: {} });

export default function FlowEditor(props) {
  return (
    <ReactFlowProvider>
      <Editor {...props} />
    </ReactFlowProvider>
  );
}

function Editor({ project, env, flow, blocks }) {
  const byKey = useMemo(() => Object.fromEntries(blocks.map((b) => [b.key, b])), [blocks]);
  const isNew = !flow;
  const [meta, setMeta] = useState(() => ({
    name: flow?.name || "",
    description: flow?.description || "",
    enabled: flow?.enabled ?? true,
    timeout: flow?.timeout ?? 60,
    record_runs: flow?.record_runs ?? true,
  }));
  const [nodes, setNodes, onNodesChange] = useNodesState((flow?.definition?.nodes || []).map(toCanvasNode));
  const [edges, setEdges, onEdgesChange] = useEdgesState((flow?.definition?.edges || []).map(toCanvasEdge));
  const [selected, setSelected] = useState(null);
  const [problems, setProblems] = useState(null);
  const [runResult, setRunResult] = useState(null);
  const [panel, setPanel] = useState("flow");
  const [run, busy] = useAction();
  const wrapper = useRef(null);
  const flowApi = useReactFlow();
  const base = envPath(project.ref, env, "/flows");

  const trace = useMemo(() => {
    const map = {};
    for (const step of runResult?.trace || []) map[step.node] = step.error ? "failed" : "ran";
    return map;
  }, [runResult]);

  const definition = () => ({
    nodes: nodes.map((n) => ({ id: n.id, position: n.position, data: { block: n.data.block, config: n.data.config || {}, label: n.data.label || undefined } })),
    edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, ...(e.sourceHandle && e.sourceHandle !== "next" ? { sourceHandle: e.sourceHandle } : {}) })),
  });

  const onConnect = useCallback((params) => {
    setEdges((eds) => addEdge(toCanvasEdge({ ...params, id: `${params.source}-${params.sourceHandle || "next"}-${params.target}` }), eds));
  }, [setEdges]);

  const addBlock = (key, position) => {
    const block = byKey[key];
    const id = uniqueId(key.split(".").pop(), nodes);
    const config = Object.fromEntries((block.config || []).filter((c) => c.default !== undefined).map((c) => [c.name, c.default]));
    const node = toCanvasNode({ id, data: { block: key, config }, position: position || { x: 80 + nodes.length * 30, y: 80 + nodes.length * 60 } });
    setNodes((ns) => [...ns, node]);
    setSelected(id);
    setPanel("node");
  };

  const onDrop = (event) => {
    event.preventDefault();
    const key = event.dataTransfer.getData("application/pawabase-block");
    if (!key) return;
    addBlock(key, flowApi.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  };

  const updateNode = (id, patch) => {
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)));
  };

  const renameNode = (id, next) => {
    if (!next || nodes.some((n) => n.id === next)) return;
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, id: next } : n)));
    setEdges((es) => es.map((e) => ({ ...e, source: e.source === id ? next : e.source, target: e.target === id ? next : e.target })));
    setSelected(next);
  };

  const validate = async () => {
    const result = await run(() => post("/flows/validate", definition()));
    if (result) setProblems(result.problems);
    return result ? result.problems : null;
  };

  const save = async () => {
    const body = { ...meta, timeout: Number(meta.timeout), definition: definition() };
    const result = await run(() => (isNew ? post(base, body) : put(`${base}/${flow.name}`, body)), "Flow saved");
    if (result && isNew) router.visit(`/projects/${project.ref}/${env}/flows/${result.name}`);
  };

  const selectedNode = nodes.find((n) => n.id === selected);
  const nodeTypes = useMemo(() => ({ block: BlockNode }), []);

  return (
    <Layout title={meta.name || "New flow"} crumbs={[<a key="f" href={`/projects/${project.ref}/${env}/flows`}>flows</a>, meta.name || "new"]} full>
      <BlocksContext.Provider value={{ byKey, trace }}>
        <div className="flow-shell">
          <Palette blocks={blocks} onAdd={addBlock} />
          <div ref={wrapper} onDrop={onDrop} onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }} style={{ position: "relative" }}>
            <div className="row" style={{ position: "absolute", zIndex: 4, top: 12, left: 12, right: 12, justifyContent: "space-between", pointerEvents: "none" }}>
              <div className="row" style={{ pointerEvents: "auto" }}>
                <b>{meta.name || "Untitled flow"}</b>
                <Badge tone={meta.enabled ? "green" : ""}>{meta.enabled ? "enabled" : "disabled"}</Badge>
                {problems && (problems.length ? <Badge tone="red">{problems.length} problem(s)</Badge> : <Badge tone="green">valid</Badge>)}
              </div>
              <div className="row" style={{ pointerEvents: "auto" }}>
                <Button onClick={validate} disabled={busy}>Validate</Button>
                {!isNew && <Button onClick={() => setPanel("run")}>Test run</Button>}
                <Button variant="primary" onClick={save} disabled={busy || !meta.name}>Save</Button>
              </div>
            </div>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_, n) => { setSelected(n.id); setPanel("node"); }}
              onPaneClick={() => { setSelected(null); setPanel((p) => (p === "node" ? "flow" : p)); }}
              deleteKeyCode={["Backspace", "Delete"]}
              fitView
              proOptions={{ hideAttribution: true }}
              colorMode="system"
            >
              <Background gap={22} size={1.5} color="var(--line-2)" />
              <Controls />
              <MiniMap pannable zoomable nodeColor="#cfc4fa" maskColor="rgba(31,27,46,0.12)" />
            </ReactFlow>
          </div>
          <aside className="inspector">
            <div style={{ padding: "8px 12px 0" }}>
              <Tabs
                value={panel}
                onChange={setPanel}
                tabs={[{ value: "flow", label: "Flow" }, ...(selectedNode ? [{ value: "node", label: "Block" }] : []), ...(!isNew ? [{ value: "run", label: "Run" }] : [])]}
              />
            </div>
            <div style={{ padding: "0 14px 20px" }} className="stack">
              {panel === "flow" && <FlowSettings meta={meta} setMeta={setMeta} isNew={isNew} problems={problems} />}
              {panel === "node" && selectedNode && (
                <NodeInspector
                  node={selectedNode}
                  block={byKey[selectedNode.data.block]}
                  onChange={(patch) => updateNode(selectedNode.id, patch)}
                  onRename={(next) => renameNode(selectedNode.id, next)}
                  onDelete={() => { setNodes((ns) => ns.filter((n) => n.id !== selectedNode.id)); setEdges((es) => es.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id)); setSelected(null); setPanel("flow"); }}
                  step={runResult?.trace?.filter((s) => s.node === selectedNode.id)}
                  nodes={nodes}
                  edges={edges}
                />
              )}
              {panel === "run" && !isNew && <RunPanel base={base} name={flow.name} nodes={nodes} result={runResult} onResult={setRunResult} />}
            </div>
          </aside>
        </div>
      </BlocksContext.Provider>
    </Layout>
  );
}

function Palette({ blocks, onAdd }) {
  const [filter, setFilter] = useState("");
  const groups = useMemo(() => {
    const q = filter.toLowerCase();
    const out = {};
    for (const b of blocks) {
      if (q && !`${b.key} ${b.title} ${b.description}`.toLowerCase().includes(q)) continue;
      (out[b.category] ||= []).push(b);
    }
    return Object.entries(out).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
  }, [blocks, filter]);
  return (
    <aside className="palette">
      <div style={{ padding: "12px 10px 4px" }}>
        <input placeholder={`Search ${blocks.length} blocks…`} value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      {groups.map(([category, items]) => (
        <div key={category} className="palette-group">
          <h4>{category}</h4>
          {items.map((b) => (
            <div
              key={b.key}
              className="palette-item"
              draggable
              onDragStart={(e) => { e.dataTransfer.setData("application/pawabase-block", b.key); e.dataTransfer.effectAllowed = "move"; }}
              onDoubleClick={() => onAdd(b.key)}
              title="Drag onto the canvas, or double-click to add"
            >
              {b.title}
              <small>{plain(b.description)}</small>
            </div>
          ))}
        </div>
      ))}
    </aside>
  );
}

function BlockNode({ id, data, selected }) {
  const { byKey, trace } = useContext(BlocksContext);
  const block = byKey[data.block] || { title: data.block, handles: ["next"], trigger: false };
  const handles = blockHandles(block, data.config);
  if (!block.trigger && !handles.includes("error")) handles.push("error");
  const summary = Object.entries(data.config || {}).slice(0, 2).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ");
  const state = trace[id];
  return (
    <div className={`flow-node ${selected ? "selected" : ""} ${block.trigger ? "trigger" : ""} ${state || ""}`}>
      {!block.trigger && <Handle type="target" position={Position.Top} />}
      <div className="flow-node-head">
        <b>{data.label || block.title}</b>
        <span className="faint mono" style={{ fontSize: 10 }}>{id}</span>
      </div>
      <div className="flow-node-body" title={summary}>{summary || data.block}</div>
      <div className="flow-handles">
        {handles.map((h) => <span key={h}>{h}</span>)}
      </div>
      {handles.map((h, i) => (
        <Handle
          key={h}
          id={h}
          type="source"
          position={Position.Bottom}
          className={h === "error" ? "error-handle" : ""}
          style={{ left: `${((i + 0.5) / handles.length) * 100}%` }}
        />
      ))}
    </div>
  );
}

function FlowSettings({ meta, setMeta, isNew, problems }) {
  const set = (k) => (e) => setMeta({ ...meta, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <>
      <Field label="Name" hint={isNew ? "Lower-case letters, digits, - and _." : "The name is permanent."}>
        <input value={meta.name} onChange={set("name")} disabled={!isNew} placeholder="checkout" />
      </Field>
      <Field label="Description"><input value={meta.description} onChange={set("description")} /></Field>
      <Field label="Timeout (seconds)"><input type="number" min="1" max="900" value={meta.timeout} onChange={set("timeout")} /></Field>
      <label className="check"><input type="checkbox" checked={meta.enabled} onChange={set("enabled")} /> Enabled</label>
      <label className="check"><input type="checkbox" checked={meta.record_runs} onChange={set("record_runs")} /> Record runs (with traces)</label>
      {problems?.length > 0 && <div className="alert error"><ul style={{ margin: 0, paddingLeft: 18 }}>{problems.map((p, i) => <li key={i}>{p}</li>)}</ul></div>}
      <div className="hint">
        Drag blocks from the left and connect handles. Every block except triggers has an <code>error</code> handle; connect it to handle failures.
        Config values accept templates like <code>{"{{ input.email }}"}</code>, <code>{"{{ steps.lookup.output.id }}"}</code> or <code>{"{{ auth.user_id }}"}</code>.
      </div>
    </>
  );
}

function NodeInspector({ node, block, onChange, onRename, onDelete, step, nodes, edges }) {
  const { byKey } = useContext(BlocksContext);
  const [id, setId] = useState(node.id);
  if (!block) return <div className="alert error">Unknown block {node.data.block}</div>;
  const config = node.data.config || {};
  const setConfig = (name, value) => {
    const next = { ...config };
    if (value === undefined || value === "") delete next[name];
    else next[name] = value;
    onChange({ config: next });
  };
  return (
    <>
      <div>
        <h3>{block.title}</h3>
        <code className="faint">{block.key}</code>
        <p className="muted" style={{ margin: "6px 0 0", fontSize: 12.5 }}>{plain(block.description)}</p>
      </div>
      <Field label="Node id" hint="Other blocks read this node's output as steps.<id>.output.">
        <input value={id} onChange={(e) => setId(e.target.value)} onBlur={() => onRename(id)} />
      </Field>
      <Field label="Label"><input value={node.data.label || ""} onChange={(e) => onChange({ label: e.target.value })} placeholder={block.title} /></Field>
      {(block.config || []).map((spec) => (
        <ConfigField key={`${node.id}-${spec.name}`} spec={spec} value={config[spec.name]} onChange={(v) => setConfig(spec.name, v)} />
      ))}
      <AvailableVariables nodeId={node.id} nodes={nodes} edges={edges} byKey={byKey} />
      {step?.length > 0 && (
        <div className="stack" style={{ gap: 6 }}>
          <h3>Last run</h3>
          {step.map((s, i) => <Json key={i} value={{ handle: s.handle, duration_ms: s.duration_ms, output: s.output, error: s.error }} />)}
        </div>
      )}
      <Button variant="danger" onClick={onDelete}>Remove block</Button>
    </>
  );
}

function AvailableVariables({ nodeId, nodes, edges, byKey }) {
  const getPredecessors = () => {
    const preds = new Set();
    const visit = (id) => {
      for (const e of edges) {
        if (e.target === id && !preds.has(e.source)) {
          preds.add(e.source);
          visit(e.source);
        }
      }
    };
    visit(nodeId);
    return Array.from(preds);
  };

  const predecessors = getPredecessors();
  const hasTrigger = nodes.some((n) => byKey[n.data.block]?.trigger);

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
  };

  const variables = [
    hasTrigger && { label: "input", path: "{{ input }}", desc: "Trigger input data" },
    { label: "auth", path: "{{ auth }}", desc: "Authentication context (user_id, roles, etc.)" },
    { label: "params", path: "{{ params }}", desc: "URL/route parameters" },
    ...predecessors.map((id) => ({
      label: `steps.${id}`,
      path: `{{ steps.${id}.output }}`,
      desc: `Output from block "${id}"`,
    })),
  ].filter(Boolean);

  if (variables.length === 0) return null;

  return (
    <div className="stack" style={{ gap: 6, paddingTop: 12, borderTop: "1px solid var(--line-2)" }}>
      <h3 style={{ marginBottom: 0 }}>Available variables</h3>
      <div style={{ display: "grid", gap: 6 }}>
        {variables.map((v) => (
          <div
            key={v.label}
            style={{
              padding: "8px 10px",
              backgroundColor: "var(--bg-2)",
              borderRadius: 4,
              cursor: "pointer",
              fontSize: 12,
              transition: "background-color 0.2s",
            }}
            onClick={() => copyToClipboard(v.path)}
            onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "var(--bg-3)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "var(--bg-2)"; }}
            title={`Click to copy: ${v.path}`}
          >
            <div style={{ fontFamily: "monospace", fontSize: 11, color: "var(--accent)" }}>{v.path}</div>
            <div style={{ color: "var(--text-secondary)", fontSize: 11, marginTop: 2 }}>{v.desc}</div>
          </div>
        ))}
      </div>
      <div className="hint" style={{ fontSize: 11 }}>
        Click any variable to copy. Reference nested values with <code>{"{{ steps.id.output.field }}"}</code>.
      </div>
    </div>
  );
}

function ConfigField({ spec, value, onChange }) {
  const label = <span>{spec.name}{spec.required && <span style={{ color: "var(--danger)" }}> *</span>}</span>;
  if (spec.enum) {
    return (
      <Field label={label} hint={spec.description}>
        <select value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)}>
          <option value="">—</option>
          {spec.enum.map((o) => <option key={o}>{o}</option>)}
        </select>
      </Field>
    );
  }
  if (spec.type === "boolean") {
    return <label className="check"><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {spec.name}{spec.description && <span className="hint"> — {spec.description}</span>}</label>;
  }
  if (spec.type === "integer" || spec.type === "number") {
    return <Field label={label} hint={spec.description}><input type="number" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} /></Field>;
  }
  if (spec.type === "json" || spec.type === "object" || spec.type === "array" || spec.widget === "condition") {
    return <Field label={label} hint={spec.description || "JSON; strings may contain {{ templates }}."}><JsonInput value={value} rows={5} onChange={(v) => onChange(v ?? undefined)} /></Field>;
  }
  if (spec.widget === "code" || spec.type === "text") {
    return <Field label={label} hint={spec.description}><textarea rows={6} value={value ?? ""} onChange={(e) => onChange(e.target.value)} /></Field>;
  }
  return <Field label={label} hint={spec.description}><input value={value ?? ""} onChange={(e) => onChange(e.target.value)} /></Field>;
}

function RunPanel({ base, name, nodes, result, onResult }) {
  const triggers = nodes.filter((n) => n.data.block?.startsWith("trigger."));
  const [input, setInput] = useState({});
  const [entry, setEntry] = useState("");
  const [asUser, setAsUser] = useState(null);
  const [run, busy] = useAction();
  return (
    <>
      <Field label="Input (JSON)"><JsonInput value={input} rows={6} onChange={setInput} /></Field>
      {triggers.length > 1 && (
        <Field label="Start from">
          <select value={entry} onChange={(e) => setEntry(e.target.value)}>
            <option value="">first trigger</option>
            {triggers.map((t) => <option key={t.id}>{t.id}</option>)}
          </select>
        </Field>
      )}
      <Field label="Run as (optional auth context)" hint={'e.g. {"authenticated": true, "user_id": "42", "roles": ["admin"]}. Empty runs with service rights.'}>
        <JsonInput value={asUser ?? undefined} rows={3} onChange={setAsUser} />
      </Field>
      <Button variant="primary" disabled={busy} onClick={async () => {
        const r = await run(() => post(`${base}/${name}/run`, { input, entry: entry || null, as_user: asUser || null }));
        if (r) onResult(r);
      }}>Run saved flow</Button>
      <div className="hint">Runs the saved version. Save first to test changes.</div>
      {result && (
        <div className="stack" style={{ gap: 8 }}>
          <div className="row"><b>Result</b><Badge tone={result.status === "succeeded" ? "green" : "red"}>{result.status}</Badge></div>
          {result.error && <div className="alert error">{result.error} {result.node && <>at <code>{result.node}</code></>}</div>}
          {result.response && <Json value={result.response} />}
          {result.result !== undefined && !result.response && <Json value={result.result} />}
          <h3>Trace</h3>
          {(result.trace || []).map((s, i) => (
            <div key={i} className={`trace-step ${s.error ? "failed" : ""}`}>
              <div className="spread"><code>{s.node}</code><span className="faint">{s.duration_ms} ms → {s.handle || "end"}</span></div>
              {s.error ? <div className="error-text">{s.error}</div> : <pre className="faint" style={{ fontSize: 11 }}>{typeof s.output === "string" ? s.output : JSON.stringify(s.output)}</pre>}
            </div>
          ))}
          {result.logs?.length > 0 && <><h3>Logs</h3><Json value={result.logs} /></>}
        </div>
      )}
    </>
  );
}

function blockHandles(block, config) {
  let handles = [...(block.handles || ["next"])];
  if (handles.includes("*")) {
    // Switch-like blocks follow a handle per case, named by the config.
    const cases = config?.cases;
    const names = Array.isArray(cases) ? cases.map(String) : Object.keys(cases || {});
    handles = [...names, "default"];
  }
  return handles;
}

function toCanvasNode(node) {
  return { id: node.id, type: "block", position: node.position || { x: 0, y: 0 }, data: { ...node.data } };
}

function toCanvasEdge(edge) {
  const handle = edge.sourceHandle || "next";
  return {
    ...edge,
    sourceHandle: handle,
    label: handle === "next" ? undefined : handle,
    animated: handle === "each",
    style: handle === "error" ? { stroke: "var(--danger)" } : undefined,
  };
}

function uniqueId(base, nodes) {
  const clean = base.replace(/[^a-z0-9_]/gi, "_");
  let i = 1;
  while (nodes.some((n) => n.id === `${clean}${i === 1 ? "" : i}`)) i += 1;
  return `${clean}${i === 1 ? "" : i}`;
}
