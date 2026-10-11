import { Link } from "@inertiajs/react";
import { useEffect, useMemo, useRef, useState } from "react";
import Layout, { dataSubnav, envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, EmptyState, Loading, PageHead } from "../../components/ui";
import { CARD, buildGraph, layout, neighbours, route } from "../../lib/topology";
import { envPath, useApi } from "../../lib/api";

const OPERATIONS = ["list", "get", "create", "update", "delete"];
const clip = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

/** A resource card: its name, its key, and its fields, with the ones that point at another resource marked. */
function NodeCard({ node, box, keys, state, onEnter, onLeave, onSelect }) {
  const hidden = node.fields.length - CARD.rows;
  const isSchema = node.kind === "schema";
  const rows = node.fields.slice(0, CARD.rows);
  return (
    <g
      className={`topo-node ${isSchema ? "schema" : ""} ${state}`}
      transform={`translate(${box.x} ${box.y})`}
      onMouseEnter={() => onEnter(node.id)}
      onMouseLeave={onLeave}
      onClick={() => onSelect(node.id)}
      tabIndex={0}
      role="button"
      aria-label={`${node.name}, ${node.fields.length} fields`}
      onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && (event.preventDefault(), onSelect(node.id))}
    >
      <rect className="topo-card" width={box.w} height={box.h} rx="14" />
      <text className="topo-title" x="16" y={CARD.head / 2 + 5}>{clip(node.name, 22)}</text>
      <text className="topo-kind" x={box.w - 16} y={CARD.head / 2 + 4} textAnchor="end">{isSchema ? "schema" : node.spec.table && node.spec.table !== node.name ? node.spec.table : "table"}</text>
      <line className="topo-rule" x1="0" x2={box.w} y1={CARD.head} y2={CARD.head} />
      {rows.map((field, i) => {
        const y = CARD.head + i * CARD.row + CARD.row / 2 + 4;
        const link = keys.has(field.name);
        return (
          <g key={field.name} className={link ? "topo-field link" : "topo-field"}>
            {link && <circle cx="16" cy={y - 4} r="3" />}
            <text x={link ? 28 : 16} y={y}>{clip(field.name, 20)}</text>
            <text className="topo-type" x={box.w - 16} y={y} textAnchor="end">{field.required ? "* " : ""}{clip(field.type || "string", 12)}</text>
          </g>
        );
      })}
      {node.fields.length === 0 && <text className="topo-type" x="16" y={CARD.head + CARD.row / 2 + 4}>no fields</text>}
      {hidden > 0 && <text className="topo-type" x="16" y={CARD.head + CARD.rows * CARD.row + CARD.row / 2 + 4}>+{hidden} more</text>}
    </g>
  );
}

function Detail({ node, graph, env, onClose }) {
  const spec = node.spec;
  const incoming = graph.edges.filter((e) => e.to === node.id);
  const outgoing = graph.edges.filter((e) => e.from === node.id);
  const name = (id) => graph.nodes.get(id)?.name || id;
  return (
    <aside className="topo-detail" aria-label={`${node.name} details`}>
      <header>
        <div>
          <b>{node.name}</b>
          <small>{node.kind === "schema" ? "Schema: a reusable set of fields" : `Resource · table ${spec.table || spec.name}`}</small>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close details"><Icon name="x" size={16} /></button>
      </header>
      {spec.description && <p className="muted">{spec.description}</p>}
      {node.kind === "resource" && (
        <section>
          <h4>Who can do what</h4>
          <div className="topo-ops">
            {OPERATIONS.map((op) => {
              const setting = spec.operations?.[op];
              return (
                <div key={op} className={setting?.enabled ? "" : "off"}>
                  <span>{op}</span>
                  <b>{setting?.enabled ? (typeof setting.policy === "string" ? setting.policy : "custom") : "off"}</b>
                </div>
              );
            })}
          </div>
        </section>
      )}
      <section>
        <h4>Connections</h4>
        {incoming.length + outgoing.length === 0 && <p className="muted small">Nothing connects to this yet.</p>}
        {outgoing.map((e) => (
          <div key={e.id} className="topo-conn"><Icon name="chevronRight" size={13} /><span>{e.kind === "schema" ? "uses schema" : "belongs to"} <b>{name(e.to)}</b> through <code>{e.field}</code></span></div>
        ))}
        {incoming.map((e) => (
          <div key={e.id} className="topo-conn"><Icon name="chevronRight" size={13} className="rotate-180" /><span><b>{name(e.from)}</b> {e.kind === "schema" ? "uses it in" : "points here with"} <code>{e.field}</code></span></div>
        ))}
      </section>
      <section>
        <h4>Fields · {node.fields.length}</h4>
        <div className="topo-fields">
          {node.fields.map((f) => (
            <div key={f.name}><span>{f.name}</span><small>{f.required ? "required · " : ""}{f.unique ? "unique · " : ""}{f.type}</small></div>
          ))}
        </div>
      </section>
      <div className="row wrap" style={{ gap: 6 }}>
        {spec.realtime && <Badge tone="brand">realtime</Badge>}
        {spec.events === false ? <Badge>no events</Badge> : <Badge>emits events</Badge>}
        {spec.cache_ttl ? <Badge>cache {spec.cache_ttl}s</Badge> : null}
      </div>
      <Link href={envHref(env, node.kind === "schema" ? "schemas" : "resources")} className="btn">Open in {node.kind === "schema" ? "Schemas" : "Resources"}</Link>
    </aside>
  );
}

export default function Topology({ env }) {
  const resources = useApi(envPath(env, "/resources"), { params: { limit: 500 } });
  const schemas = useApi(envPath(env, "/schemas"), { params: { limit: 500 } });
  const [hover, setHover] = useState(null);
  const [selected, setSelected] = useState(null);
  const [zoom, setZoom] = useState(1);
  const [query, setQuery] = useState("");
  const frame = useRef(null);

  const ready = resources.data && schemas.data;
  const graph = useMemo(() => (ready ? buildGraph(resources.data.data || [], schemas.data.data || []) : null), [ready, resources.data, schemas.data]);
  const grid = useMemo(() => (graph ? layout(graph) : null), [graph]);

  // Open at a size that shows the whole diagram, when it is wider than the room it has.
  useEffect(() => {
    if (!grid || !frame.current) return;
    setZoom(Math.min(1, Math.max(0.5, (frame.current.clientWidth - 24) / grid.width)));
  }, [grid]);

  const focus = hover || selected;
  const near = useMemo(() => (graph && focus ? neighbours(graph, focus) : null), [graph, focus]);
  const needle = query.trim().toLowerCase();
  const keyed = useMemo(() => {
    const map = new Map();
    if (graph) for (const edge of graph.edges) {
      if (!map.has(edge.from)) map.set(edge.from, new Set());
      map.get(edge.from).add(edge.field);
    }
    return map;
  }, [graph]);

  const fit = () => {
    if (!grid || !frame.current) return;
    setZoom(Math.min(1.2, Math.max(0.4, (frame.current.clientWidth - 8) / grid.width)));
  };
  const chosen = selected && graph?.nodes.get(selected);
  const empty = graph && graph.nodes.size === 0;

  return (
    <Layout title="Topology" subnav={dataSubnav(env, "topology")}>
      <PageHead
        title="Topology"
        description="How your data fits together: resources as tables, the relations between them, and the schemas they use. Point at one to see what it touches."
        actions={
          graph && !empty && (
            <div className="row" style={{ gap: 8 }}>
              <div className="topo-search"><Icon name="search" size={14} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a resource" aria-label="Find a resource" /></div>
              <div className="topo-zoom" role="group" aria-label="Zoom">
                <button type="button" onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.1).toFixed(2)))} aria-label="Zoom out">−</button>
                <button type="button" onClick={fit} title="Fit to width">{Math.round(zoom * 100)}%</button>
                <button type="button" onClick={() => setZoom((z) => Math.min(1.6, +(z + 0.1).toFixed(2)))} aria-label="Zoom in">+</button>
              </div>
            </div>
          )
        }
      />
      <Loading state={resources}>
        {() => !graph ? null : empty ? (
          <Card>
            <EmptyState icon="resources" title="Nothing to draw yet" action={<Link href={envHref(env, "resources")} className="btn primary">Create a resource</Link>}>
              Define resources and relate them to each other, and their shape appears here.
            </EmptyState>
          </Card>
        ) : (
          <div className={`topo ${chosen ? "with-detail" : ""}`}>
            <Card flush className="topo-canvas">
              <div className="topo-frame" ref={frame} onClick={(e) => e.target === e.currentTarget && setSelected(null)}>
                <svg width={grid.width * zoom} height={grid.height * zoom} viewBox={`0 0 ${grid.width} ${grid.height}`} role="img" aria-label="Diagram of resources and their relations">
                  <defs>
                    <marker id="topo-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" /></marker>
                  </defs>
                  {graph.edges.map((edge) => {
                    const path = route(edge, graph, grid.positions);
                    if (!path) return null;
                    const lit = near?.edgeIds.has(edge.id);
                    return (
                      <g key={edge.id} className={`topo-edge ${edge.kind} ${lit ? "lit" : near ? "dim" : ""}`}>
                        <path d={path.d} {...(edge.kind === "schema" ? { markerEnd: "url(#topo-arrow)" } : { markerStart: "url(#topo-arrow)" })} />
                        {lit && <text x={path.x} y={path.y - 6} textAnchor="middle">{edge.field}</text>}
                      </g>
                    );
                  })}
                  {[...graph.nodes.values()].map((node) => {
                    const box = grid.positions.get(node.id);
                    if (!box) return null;
                    const matches = needle && node.name.toLowerCase().includes(needle);
                    const state = [
                      selected === node.id ? "selected" : "",
                      near ? (near.ids.has(node.id) ? "near" : "dim") : "",
                      needle ? (matches ? "match" : "dim") : "",
                    ].join(" ");
                    return <NodeCard key={node.id} node={node} box={box} keys={keyed.get(node.id) || new Set()} state={state} onEnter={setHover} onLeave={() => setHover(null)} onSelect={(id) => setSelected((s) => (s === id ? null : id))} />;
                  })}
                </svg>
              </div>
              <div className="topo-legend">
                <span><i className="solid" />Arrow points at the resource a field refers to</span>
                <span><i className="dashed" />Uses a schema</span>
                <span>{[...graph.nodes.values()].filter((n) => n.kind === "resource").length} resources · {graph.edges.length} connections</span>
              </div>
            </Card>
            {chosen && <Detail node={chosen} graph={graph} env={env} onClose={() => setSelected(null)} />}
          </div>
        )}
      </Loading>
      {graph?.dangling.length > 0 && (
        <div className="alert warn" style={{ marginTop: 12 }}>
          {graph.dangling.map((d) => `${d.from}.${d.relation} points at "${d.to}", which does not exist`).join(" · ")}
        </div>
      )}
    </Layout>
  );
}
