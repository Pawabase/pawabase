import { router } from "@inertiajs/react";
import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider,
  addEdge, useEdgesState, useNodesInitialized, useNodesState, useReactFlow,
} from "@xyflow/react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Field, Json, JsonInput, Segmented, useAction } from "../../components/ui";
import { envPath, post, put } from "../../lib/api";
import { arrange, isCramped } from "../../lib/flowLayout";

const CATEGORY_ORDER = ["triggers", "control", "resources", "responses", "auth", "data", "code", "events", "jobs", "mail", "storage", "realtime", "cache", "integrations", "observability"];
const rank = (c) => (CATEGORY_ORDER.includes(c) ? CATEGORY_ORDER.indexOf(c) : 99);
const plain = (text) => (text || "").replace(/``/g, "");
const BlocksContext = createContext({ byKey: {}, trace: {} });

const BLOCK_ICONS = {
  "auth.lookup": "users",
  "auth.require": "secrets",
  "auth.user": "users",
  "policy.check": "policies",
  "cache.delete": "trash",
  "cache.get": "import",
  "cache.invalidate": "refresh",
  "cache.set": "upload",
  "control.delay": "clock",
  "control.foreach": "layers",
  "control.if": "gitBranch",
  "control.merge": "transformers",
  "control.noop": "flowBlock",
  "control.retry": "refresh",
  "control.set": "braces",
  "control.stop": "x",
  "control.switch": "routes",
  "control.timeout": "status",
  "control.while": "chevronsUpDown",
  "crypto.hash": "secrets",
  "json.parse": "braces",
  "json.stringify": "code",
  "math.calculate": "usage",
  "time.now": "clock",
  "transform.batch": "layers",
  "transform.filter": "transformers",
  "transform.flatten": "transformers",
  "transform.group": "transformers",
  "transform.map": "transformers",
  "transform.pick": "transformers",
  "transform.rename": "transformers",
  "transform.slice": "transformers",
  "transform.sort": "transformers",
  "transform.summarize": "transformers",
  "transform.template": "transformers",
  "transform.unique": "transformers",
  "util.id": "keys",
  "validate.schema": "schemas",
  "event.emit": "events",
  "http.request": "networking",
  "secret.get": "secrets",
  "webhook.send": "send",
  "flow.call": "flows",
  "queue.flow": "send",
  "mail.send": "mail",
  "log.write": "audit",
  "metric.increment": "chart",
  "realtime.publish": "realtime",
  "db.query": "database",
  "db.transaction": "schemas",
  "resource.create": "plus",
  "resource.delete": "trash",
  "resource.get": "eye",
  "resource.list": "resources",
  "resource.update": "chevronsUpDown",
  "error.raise": "x",
  "response.return": "send",
  "storage.delete": "trash",
  "storage.put": "upload",
  "storage.read": "eye",
  "storage.signed_url": "link",
  "trigger.event": "events",
  "trigger.http": "networking",
  "trigger.job": "jobs",
  "trigger.manual": "play",
  "trigger.realtime": "realtime",
  "trigger.resource": "resources",
  "trigger.schedule": "schedules",
  "trigger.webhook": "webhooks",
};

const CATEGORY_ICONS = {
  triggers: "bolt",
  control: "routes",
  resources: "resources",
  responses: "send",
  auth: "secrets",
  data: "database",
  code: "code",
  events: "events",
  jobs: "jobs",
  mail: "mail",
  storage: "storage",
  realtime: "realtime",
  cache: "refresh",
  integrations: "networking",
  observability: "observability",
};

function blockIcon(block) {
  if (!block) return "flowBlock";
  return BLOCK_ICONS[block.key] || CATEGORY_ICONS[block.category] || "flowBlock";
}

export default function FlowEditor(props) {
  return (
    <ReactFlowProvider>
      <Editor {...props} />
    </ReactFlowProvider>
  );
}

function Editor({ env, flow, blocks }) {
  const byKey = useMemo(() => Object.fromEntries(blocks.map((b) => [b.key, b])), [blocks]);
  const isNew = !flow;
  const [meta, setMeta] = useState(() => ({
    name: flow?.name || "",
    description: flow?.description || "",
    enabled: flow?.enabled ?? true,
    timeout: flow?.timeout ?? 60,
    record_runs: flow?.record_runs ?? true,
  }));
  // A flow made by code has every block at the same spot: open it already arranged.
  const start = useMemo(() => {
    const first = (flow?.definition?.nodes || []).map(toCanvasNode);
    const joined = (flow?.definition?.edges || []).map(toCanvasEdge);
    const cramped = isCramped(first);
    return { nodes: cramped ? tidy(first, joined, byKey) : first, edges: joined, cramped };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [nodes, setNodes, onNodesChange] = useNodesState(start.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(start.edges);
  const [selected, setSelected] = useState(null);
  const [problems, setProblems] = useState(null);
  const [runResult, setRunResult] = useState(null);
  const [panel, setPanel] = useState("flow");
  const [run, busy] = useAction();
  const wrapper = useRef(null);
  const flowApi = useReactFlow();
  const base = envPath(env, "/flows");

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

  // The first arrangement had to guess how big each block is; once they are on screen, do it again with their real sizes.
  const measured = useNodesInitialized();
  const refined = useRef(false);
  useEffect(() => {
    if (!start.cramped || !measured || refined.current) return;
    refined.current = true;
    setNodes((ns) => tidy(ns, edges, byKey));
    window.requestAnimationFrame(() => flowApi.fitView({ padding: 0.2 }));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [measured]);

  const tidyUp = () => {
    setNodes((ns) => tidy(ns, edges, byKey));
    window.requestAnimationFrame(() => flowApi.fitView({ padding: 0.2, duration: 250 }));
  };

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
    if (result && isNew) router.visit(envHref(env, "flows", result.name));
  };

  const selectedNode = nodes.find((n) => n.id === selected);
  const nodeTypes = useMemo(() => ({ block: BlockNode }), []);

  return (
    <Layout title={meta.name || "New flow"} crumbs={[<a key="f" href={envHref(env, "flows")}>flows</a>, meta.name || "new"]} full flowPage>
      <BlocksContext.Provider value={{ byKey, trace }}>
        <div className="flow-shell grid grid-cols-[248px_minmax(0,_1fr)_304px] h-full min-h-0 w-full p-0! max-navigation:grid-cols-1">
          <Palette blocks={blocks} onAdd={addBlock} />
          <div className="flow-canvas" ref={wrapper} onDrop={onDrop} onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}>
            <div className="flow-toolbar absolute z-[4] top-0 left-0 right-0 flex items-center justify-between gap-3 pointer-events-auto h-[44px] p-[0_10px_0_14px] bg-canvas [border-bottom:1px_solid_var(--line)]">
              <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 flow-toolbar-title pointer-events-auto min-w-0 p-0 bg-none border-0 rounded-none [&_b]:max-w-[220px] [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap gap-2">
                <Icon name="flowBlock" size={18} />
                <b>{meta.name || "Untitled flow"}</b>
                <Badge tone={meta.enabled ? "green" : ""}>{meta.enabled ? "enabled" : "disabled"}</Badge>
                {problems && (problems.length ? <Badge tone="red">{problems.length} problem(s)</Badge> : <Badge tone="green">valid</Badge>)}
              </div>
              <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 flow-toolbar-actions pointer-events-auto gap-1.5 [&_.btn]:p-[0_10px] [&_.btn]:text-ui-sm [&_.btn]:h-control-sm">
                <Button onClick={tidyUp} title="Arrange the blocks neatly, top to bottom"><Icon name="layers" size={14} />Arrange</Button>
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
              <MiniMap pannable zoomable nodeColor="#3a211c" nodeStrokeColor="#9a8cff" maskColor="rgba(8,9,10,0.55)" />
            </ReactFlow>
          </div>
          <aside className="inspector bg-canvas overflow-auto [border-left:1px_solid_var(--line)] max-navigation:hidden">
            <div className="inspector-tabs p-[0_14px]">
              <Segmented
                value={panel}
                onChange={setPanel}
                options={[["flow", "Flow"], ...(selectedNode ? [["node", "Block"]] : []), ...(!isNew ? [["run", "Run"]] : [])]}
              />
            </div>
            <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 inspector-body">
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
    <aside className="subnav palette w-[248px] shrink-0 flex flex-col bg-canvas [border-right:1px_solid_var(--line)] z-[20] h-full min-h-0 max-navigation:hidden" aria-label="Blocks">
      <div className="subnav-head h-[var(--bar)] box-border flex items-center justify-between p-[0_12px_0_16px] [border-bottom:1px_solid_var(--line)] text-base [&_b]:font-semibold">
        <b>Blocks</b>
        <span className="subnav-count text-2xs font-semibold text-faint">{blocks.length}</span>
      </div>
      <div className="subnav-search flex items-center gap-2 m-[12px_12px_4px] p-[0_10px] [border:1px_solid_var(--line-2)] rounded-sm bg-transparent text-faint [&_input]:flex-1 [&_input]:min-w-0 [&_input]:border-0 [&_input]:outline-none [&_input]:shadow-none [&_input]:bg-transparent [&_input]:p-[8px_0] [&_input]:text-ui [&:focus-within]:border-brand h-control">
        <Icon name="search" size={14} />
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search blocks…" aria-label="Search blocks" />
      </div>
      <nav className="subnav-list flex-1 min-h-0 overflow-y-auto p-[8px_10px]">
        {groups.length === 0 && <p className="subnav-empty m-[16px_10px] text-muted text-ui">No blocks match.</p>}
        {groups.map(([category, items]) => (
          <div key={category} className="subnav-group m-[0_-10px] p-[4px_10px_8px] [&_+_.subnav-group]:mt-1.5 [&_+_.subnav-group]:[border-top:1px_solid_var(--line-2)] [&_+_.subnav-group]:pt-1.5">
            <div className="palette-title flex items-center justify-between p-[10px_10px_6px] text-2xs font-semibold-plus uppercase tracking-[0.06em] text-muted [&::after]:hidden">
              <span>{category}</span>
              <span>{items.length}</span>
            </div>
            {items.map((b) => (
              <button
                key={b.key}
                type="button"
                draggable
                onDragStart={(e) => { e.dataTransfer.setData("application/pawabase-block", b.key); e.dataTransfer.effectAllowed = "move"; }}
                onDoubleClick={() => onAdd(b.key)}
                title={`${plain(b.description)} — drag onto the canvas, or double-click to add`}
                className="subnav-item relative flex h-auto min-h-8 w-full cursor-grab items-center gap-2 rounded-sm border-0 bg-none p-[7px_10px] text-left font-inherit text-body font-medium text-muted shadow-none [&:hover]:bg-hover [&:hover]:text-text [&:hover_.palette-icon]:text-brand"
              >
                <Icon name={blockIcon(b)} size={15} className="palette-icon shrink-0 text-faint" />
                <span className="flex-1 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">{b.title}</span>
                <span className="subnav-tag shrink-0 text-[10.5px] text-faint rounded-xs">{b.key.split(".").pop()}</span>
              </button>
            ))}
          </div>
        ))}
      </nav>
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
    <div className={`flow-node bg-surface [border:1px_solid_var(--line-2)] rounded-sm min-w-[170px] max-w-[240px] text-xs [&.selected]:border-brand [&.selected]:shadow-none [&.trigger_.flow-node-head]:bg-head-bg [&.trigger_.flow-node-head]:text-text [&.trigger_.flow-node-head]:rounded-[var(--r)_var(--r)_0_0] [&.failed]:border-danger [&.ran]:outline-[1px_solid_var(--ok)] shadow-none [&.trigger_.flow-node-head]:shadow-none [&.trigger_.flow-node-head]:[border-top:2px_solid_var(--brand)] [&.ran]:outline-offset-[2px] ${selected ? "selected" : ""} ${block.trigger ? "trigger" : ""} ${state || ""}`}>
      {!block.trigger && <Handle type="target" position={Position.Top} />}
      <div className="flow-node-head p-[7px_9px] [border-bottom:1px_solid_var(--line)] flex items-center justify-between gap-1.5 font-semibold-plus rounded-[var(--r)_var(--r)_0_0] bg-head-bg text-text">
        <span className="flow-node-title flex items-center gap-[7px] min-w-0 [&_.icon]:text-brand [&_.icon]:flex-none"><Icon name={blockIcon(block)} size={15} /><b>{data.label || block.title}</b></span>
        <span className="faint text-faint mono font-mono text-ui-sm" style={{ fontSize: 10 }}>{id}</span>
      </div>
      <div className="flow-node-body p-[7px_11px] text-muted font-mono text-2xs overflow-hidden text-ellipsis whitespace-nowrap" title={summary}>{summary || data.block}</div>
      <div className="flow-handles flex justify-around p-[4px_6px_8px] text-3xs text-faint">
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
      <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui"><input type="checkbox" checked={meta.enabled} onChange={set("enabled")} /> Enabled</label>
      <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui"><input type="checkbox" checked={meta.record_runs} onChange={set("record_runs")} /> Record runs (with traces)</label>
      {problems?.length > 0 && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error"><ul style={{ margin: 0, paddingLeft: 18 }}>{problems.map((p, i) => <li key={i}>{p}</li>)}</ul></div>}
      <div className="hint text-xs font-book text-muted leading-[1.45]">
        Drag blocks from the left and connect handles. Every block except triggers has an <code>error</code> handle; connect it to handle failures.
        Config values accept templates like <code>{"{{ input.email }}"}</code>, <code>{"{{ steps.lookup.output.id }}"}</code> or <code>{"{{ auth.user_id }}"}</code>.
      </div>
    </>
  );
}

function NodeInspector({ node, block, onChange, onRename, onDelete, step, nodes, edges }) {
  const { byKey } = useContext(BlocksContext);
  const [id, setId] = useState(node.id);
  const [focusedField, setFocusedField] = useState(null);
  const fieldRefs = useRef({});

  if (!block) return <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">Unknown block {node.data.block}</div>;
  const config = node.data.config || {};
  const setConfig = (name, value) => {
    const next = { ...config };
    if (value === undefined || value === "") delete next[name];
    else next[name] = value;
    onChange({ config: next });
  };

  const insertVariable = (variable) => {
    if (!focusedField || !fieldRefs.current[focusedField]) return;
    const el = fieldRefs.current[focusedField];
    const start = el.selectionStart || 0;
    const end = el.selectionEnd || 0;
    const before = (el.value || "").substring(0, start);
    const after = (el.value || "").substring(end);
    const newValue = before + variable + after;
    setConfig(focusedField, newValue);
    setTimeout(() => {
      el.focus();
      el.setSelectionRange(start + variable.length, start + variable.length);
    }, 0);
  };

  return (
    <>
      <div>
        <h3>{block.title}</h3>
        <code className="faint text-faint">{block.key}</code>
        <p className="muted text-muted" style={{ margin: "6px 0 0", fontSize: 12.5 }}>{plain(block.description)}</p>
      </div>
      <Field label="Node id" hint="Other blocks read this node's output as steps.<id>.output.">
        <input value={id} onChange={(e) => setId(e.target.value)} onBlur={() => onRename(id)} />
      </Field>
      <Field label="Label"><input value={node.data.label || ""} onChange={(e) => onChange({ label: e.target.value })} placeholder={block.title} /></Field>
      {(block.config || []).map((spec) => (
        <ConfigField
          key={`${node.id}-${spec.name}`}
          spec={spec}
          value={config[spec.name]}
          onChange={(v) => setConfig(spec.name, v)}
          fieldRef={(el) => { if (el) fieldRefs.current[spec.name] = el; }}
          onFocus={() => setFocusedField(spec.name)}
          onBlur={() => setFocusedField(null)}
        />
      ))}
      <AvailableVariables
        nodeId={node.id}
        nodes={nodes}
        edges={edges}
        byKey={byKey}
        focusedField={focusedField}
        onInsert={insertVariable}
      />
      {step?.length > 0 && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 6 }}>
          <h3>Last run</h3>
          {step.map((s, i) => <Json key={i} value={{ handle: s.handle, duration_ms: s.duration_ms, output: s.output, error: s.error }} />)}
        </div>
      )}
      <Button variant="danger" onClick={onDelete}>Remove block</Button>
    </>
  );
}

function AvailableVariables({ nodeId, nodes, edges, byKey, focusedField, onInsert }) {
  const getPredecessors = () => {
    const preds = [];
    const visited = new Set();
    const visit = (id) => {
      if (visited.has(id)) return;
      visited.add(id);
      for (const e of edges) {
        if (e.target === id) {
          preds.push(e.source);
          visit(e.source);
        }
      }
    };
    visit(nodeId);
    return preds;
  };

  const predecessors = getPredecessors();
  const hasTrigger = nodes.some((n) => byKey[n.data.block]?.trigger);

  const variables = [
    hasTrigger && { label: "input", path: "{{ input }}" },
    { label: "auth", path: "{{ auth }}" },
    { label: "params", path: "{{ params }}" },
    ...predecessors.map((id) => ({
      label: `steps.${id}`,
      path: `{{ steps.${id}.output }}`,
    })),
  ].filter(Boolean);

  return (
    <div style={{ paddingTop: 12, borderTop: "1px solid var(--line-2)", marginTop: 12 }}>
      <h3 style={{ margin: "0 0 10px 0", fontSize: 13, fontWeight: 600 }}>
        Variables {focusedField && <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-secondary)" }}>in {focusedField}</span>}
      </h3>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {variables.map((v) => (
          <button
            key={v.label}
            onClick={() => focusedField && onInsert(v.path)}
            style={{
              padding: "4px 10px",
              backgroundColor: focusedField ? "var(--accent)" : "var(--bg-2)",
              color: focusedField ? "white" : "var(--text-primary)",
              border: "none",
              borderRadius: "var(--r)",
              cursor: focusedField ? "pointer" : "default",
              fontSize: 11,
              fontFamily: "monospace",
              fontWeight: 500,
              transition: "all 0.2s",
              opacity: focusedField ? 1 : 0.6,
              whiteSpace: "nowrap",
              pointerEvents: focusedField ? "auto" : "none",
            }}
            onMouseEnter={(e) => { if (focusedField) e.currentTarget.style.opacity = "0.9"; }}
            onMouseLeave={(e) => { if (focusedField) e.currentTarget.style.opacity = "1"; }}
            title={focusedField ? `Insert: ${v.path}` : "Focus a field to use variables"}
          >
            {v.path}
          </button>
        ))}
      </div>
      {!focusedField && <div className="hint text-xs font-book text-muted leading-[1.45]" style={{ fontSize: 10, marginTop: 8, color: "var(--text-secondary)" }}>Focus a field to insert variables</div>}
    </div>
  );
}

function ConfigField({ spec, value, onChange, fieldRef, onFocus, onBlur }) {
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
    return <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui"><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {spec.name}{spec.description && <span className="hint text-xs font-book text-muted leading-[1.45]"> — {spec.description}</span>}</label>;
  }
  if (spec.type === "integer" || spec.type === "number") {
    return <Field label={label} hint={spec.description}><input type="number" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} /></Field>;
  }
  if (spec.type === "json" || spec.type === "object" || spec.type === "array" || spec.widget === "condition") {
    return <Field label={label} hint={spec.description || "JSON; strings may contain {{ templates }}."}><JsonInput value={value} rows={5} onChange={(v) => onChange(v ?? undefined)} /></Field>;
  }
  if (spec.widget === "code" || spec.type === "text") {
    return <Field label={label} hint={spec.description}><textarea ref={fieldRef} rows={6} value={value ?? ""} onChange={(e) => onChange(e.target.value)} onFocus={onFocus} onBlur={onBlur} /></Field>;
  }
  return <Field label={label} hint={spec.description}><input ref={fieldRef} value={value ?? ""} onChange={(e) => onChange(e.target.value)} onFocus={onFocus} onBlur={onBlur} /></Field>;
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
      <div className="hint text-xs font-book text-muted leading-[1.45]">Runs the saved version. Save first to test changes.</div>
      {result && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 8 }}>
          <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><b>Result</b><Badge tone={result.status === "succeeded" ? "green" : "red"}>{result.status}</Badge></div>
          {result.error && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{result.error} {result.node && <>at <code>{result.node}</code></>}</div>}
          {result.response && <Json value={result.response} />}
          {result.result !== undefined && !result.response && <Json value={result.result} />}
          <h3>Trace</h3>
          {(result.trace || []).map((s, i) => (
            <div key={i} className={`trace-step [border-left:2px_solid_var(--line-2)] p-[2px_0_8px_10px] text-xs [&.failed]:border-l-danger ${s.error ? "failed" : ""}`}>
              <div className="spread flex items-center justify-between gap-3"><code>{s.node}</code><span className="faint text-faint">{s.duration_ms} ms → {s.handle || "end"}</span></div>
              {s.error ? <div className="error-text text-danger text-ui-sm font-medium">{s.error}</div> : <pre className="faint text-faint" style={{ fontSize: 11 }}>{typeof s.output === "string" ? s.output : JSON.stringify(s.output)}</pre>}
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

/** The blocks with new positions from :func:`arrange`, using the sizes they have on screen and the order of each block's outputs. */
function tidy(nodes, edges, byKey) {
  const handles = (id) => {
    const node = nodes.find((n) => n.id === id);
    const block = node && byKey[node.data.block];
    if (!block) return [];
    const list = blockHandles(block, node.data.config);
    if (!block.trigger && !list.includes("error")) list.push("error");
    return list;
  };
  const sizes = Object.fromEntries(nodes.filter((n) => n.measured?.width).map((n) => [n.id, { width: n.measured.width, height: n.measured.height }]));
  const positions = arrange(nodes, edges, sizes, handles);
  return nodes.map((n) => ({ ...n, position: positions[n.id] || n.position }));
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
