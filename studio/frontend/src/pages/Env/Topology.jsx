import { Link } from "@inertiajs/react";
import { useEffect, useMemo, useRef, useState } from "react";
import Layout, { envHref } from "../../components/Layout";
import { tablesHref, useDataSubnav } from "../../components/DataNav";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, EmptyState, Loading, PageHead, useAction } from "../../components/ui";
import { CARD, buildGraph, layout, neighbours, route } from "../../lib/topology";
import { editable } from "../../lib/kinds";
import { envPath, put, useApi } from "../../lib/api";

const OPERATIONS = ["list", "get", "create", "update", "delete"];
const clip = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

/** A resource card: its name, its key, and its fields, with the ones that point at another resource marked. */
function NodeCard({ node, box, keys, state, onEnter, onLeave, onSelect }) {
  const hidden = node.fields.length - CARD.rows;
  const isSchema = node.kind === "schema";
  const rows = node.fields.slice(0, CARD.rows);
  return (
    <g
      className={`topo-node cursor-pointer outline-none transition-[opacity_0.18s] [&:hover_.topo-card]:stroke-brand [&:hover_.topo-card]:[stroke-width:1.6] [&:focus-visible_.topo-card]:stroke-brand [&:focus-visible_.topo-card]:[stroke-width:1.6] [&.selected_.topo-card]:stroke-brand [&.selected_.topo-card]:[stroke-width:1.6] [&.match_.topo-card]:stroke-brand [&.match_.topo-card]:[stroke-width:1.6] [&.dim]:opacity-[0.32] [&.schema_.topo-card]:[stroke-dasharray:5_4] ${isSchema ? "schema" : ""} ${state}`}
      transform={`translate(${box.x} ${box.y})`}
      onMouseEnter={() => onEnter(node.id)}
      onMouseLeave={onLeave}
      onClick={() => onSelect(node.id)}
      tabIndex={0}
      role="button"
      aria-label={`${node.name}, ${node.fields.length} fields`}
      onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && (event.preventDefault(), onSelect(node.id))}
    >
      <rect className="topo-card fill-surface stroke-line-2 stroke-1 transition-[stroke_0.15s]" width={box.w} height={box.h} rx="14" />
      <text className="topo-title [font:650_14px_var(--sans)] fill-text tracking-[-0.01em]" x="16" y={CARD.head / 2 + 5}>{clip(node.name, 22)}</text>
      <text className="topo-kind [font:500_11px_var(--sans)] fill-faint" x={box.w - 16} y={CARD.head / 2 + 4} textAnchor="end">{isSchema ? "schema" : node.spec.table && node.spec.table !== node.name ? node.spec.table : "table"}</text>
      <line className="topo-rule stroke-line" x1="0" x2={box.w} y1={CARD.head} y2={CARD.head} />
      {rows.map((field, i) => {
        const y = CARD.head + i * CARD.row + CARD.row / 2 + 4;
        const link = keys.has(field.name);
        return (
          <g key={field.name} className={link ? "topo-field link" : "topo-field"}>
            {link && <circle cx="16" cy={y - 4} r="3" />}
            <text x={link ? 28 : 16} y={y}>{clip(field.name, 20)}</text>
            <text className="topo-type [font:500_11px_var(--sans)] fill-faint" x={box.w - 16} y={y} textAnchor="end">{field.required ? "* " : ""}{clip(field.type || "string", 12)}</text>
          </g>
        );
      })}
      {node.fields.length === 0 && <text className="topo-type [font:500_11px_var(--sans)] fill-faint" x="16" y={CARD.head + CARD.row / 2 + 4}>no fields</text>}
      {hidden > 0 && <text className="topo-type [font:500_11px_var(--sans)] fill-faint" x="16" y={CARD.head + CARD.rows * CARD.row + CARD.row / 2 + 4}>+{hidden} more</text>}
    </g>
  );
}

function Detail({ node, graph, env, onClose, onDeclare, busy }) {
  const spec = node.spec;
  const incoming = graph.edges.filter((e) => e.to === node.id);
  const outgoing = graph.edges.filter((e) => e.from === node.id);
  const name = (id) => graph.nodes.get(id)?.name || id;
  return (
    <aside className="topo-detail bg-surface rounded-sm p-[18px] grid gap-4 max-h-[calc(100vh_-_220px)] overflow-auto [&_header]:flex [&_header]:justify-between [&_header]:gap-2 [&_header_b]:block [&_header_b]:text-title [&_header_b]:tracking-[-0.015em] [&_header_small]:text-muted [&_header_small]:text-ui-sm [&_h4]:m-[0_0_8px] [&_h4]:text-2xs [&_h4]:font-bold [&_h4]:tracking-[0.1em] [&_h4]:uppercase [&_h4]:text-faint" aria-label={`${node.name} details`}>
      <header>
        <div>
          <b>{node.name}</b>
          <small>{node.kind === "schema" ? "Schema: a reusable set of fields" : `Resource · table ${spec.table || spec.name}`}</small>
        </div>
        <button type="button" className="icon-btn w-control h-control rounded-[calc(var(--r)_-_1px)] grid place-items-center border-0 bg-transparent text-text cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong" onClick={onClose} aria-label="Close details"><Icon name="x" size={16} /></button>
      </header>
      {spec.description && <p className="muted text-muted">{spec.description}</p>}
      {node.kind === "resource" && (
        <section>
          <h4>Who can do what</h4>
          <div className="topo-ops grid grid-cols-[repeat(auto-fill,_minmax(124px,_1fr))] gap-1.5 [&_div]:grid [&_div]:p-[8px_10px] [&_div]:[border:1px_solid_var(--line)] [&_div]:rounded-sm [&_div]:text-xs [&_div_span]:text-muted [&_div_b]:text-ui-sm [&_div_b]:font-semibold [&_div_b]:overflow-hidden [&_div_b]:text-ellipsis [&_div_b]:whitespace-nowrap [&_div.off]:opacity-50">
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
        {incoming.length + outgoing.length === 0 && <p className="muted text-muted small text-ui-sm">Nothing connects to this yet.</p>}
        {outgoing.map((e) => (
          <div key={e.id} className="topo-conn flex gap-2 items-baseline p-[4px_0] text-ui text-muted [&_code]:text-xs [&_.btn]:ml-auto [&_.btn]:flex-none">
            <Icon name="chevronRight" size={13} />
            <span>{e.inferred ? "looks like it belongs to" : e.kind === "schema" ? "uses schema" : "belongs to"} <b>{name(e.to)}</b> through <code>{e.field}</code></span>
            {e.inferred && <Button size="sm" disabled={busy} onClick={() => onDeclare(e)}>Declare</Button>}
          </div>
        ))}
        {incoming.map((e) => (
          <div key={e.id} className="topo-conn flex gap-2 items-baseline p-[4px_0] text-ui text-muted [&_code]:text-xs [&_.btn]:ml-auto [&_.btn]:flex-none"><Icon name="chevronRight" size={13} className="rotate-180 transform-[rotate(180deg)]" /><span><b>{name(e.from)}</b> {e.kind === "schema" ? "uses it in" : "points here with"} <code>{e.field}</code></span></div>
        ))}
      </section>
      <section>
        <h4>Fields · {node.fields.length}</h4>
        <div className="topo-fields [&_div]:flex [&_div]:justify-between [&_div]:gap-3 [&_div]:p-[6px_0] [&_div]:[border-bottom:1px_solid_var(--line)] [&_div]:text-ui [&_small]:text-faint [&_small]:text-xs">
          {node.fields.map((f) => (
            <div key={f.name}><span>{f.name}</span><small>{f.required ? "required · " : ""}{f.unique ? "unique · " : ""}{f.type}</small></div>
          ))}
        </div>
      </section>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 6 }}>
        {spec.realtime && <Badge tone="brand">realtime</Badge>}
        {spec.events === false ? <Badge>no events</Badge> : <Badge>emits events</Badge>}
        {spec.cache_ttl ? <Badge>cache {spec.cache_ttl}s</Badge> : null}
      </div>
      <Link href={node.kind === "schema" ? envHref(env, "schemas") : tablesHref(env, spec.table || node.name)} className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text">Open in {node.kind === "schema" ? "Schemas" : "Tables"}</Link>
    </aside>
  );
}

export default function Topology({ env }) {
  const resources = useApi(envPath(env, "/resources"), { params: { limit: 500 } });
  const schemas = useApi(envPath(env, "/schemas"), { params: { limit: 500 } });
  const { subnav } = useDataSubnav(env, "topology");
  const [hover, setHover] = useState(null);
  const [selected, setSelected] = useState(null);
  const [zoom, setZoom] = useState(1);
  const [query, setQuery] = useState("");
  const frame = useRef(null);
  const [run, busy] = useAction();

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
  // Turn a guessed connection into a declared relation on the resource that holds the key.
  const declare = async (edge) => {
    const node = graph.nodes.get(edge.from);
    const target = graph.nodes.get(edge.to);
    const body = editable(node.spec);
    body.relations = [...(body.relations || []), { name: edge.name, resource: target.name, field: edge.field, type: "belongs_to" }];
    if (await run(() => put(envPath(env, `/resources/${node.name}`), body), `${node.name}.${edge.field} now belongs to ${target.name}`)) resources.reload();
  };
  const guessed = graph ? graph.edges.filter((e) => e.inferred).length : 0;
  const chosen = selected && graph?.nodes.get(selected);
  const empty = graph && graph.nodes.size === 0;

  return (
    <Layout title="Topology" subnav={subnav}>
      <PageHead
        title="Topology"
        description="How your data fits together: resources as tables, the relations between them, and the schemas they use. Point at one to see what it touches."
        actions={
          graph && !empty && (
            <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 8 }}>
              <div className="topo-search flex items-center gap-2 h-[34px] p-[0_12px] [border:1px_solid_var(--line-2)] rounded-sm bg-surface text-faint [&_input]:border-0 [&_input]:outline-hidden [&_input]:bg-none [&_input]:font-inherit [&_input]:w-[150px] [&_input]:text-text"><Icon name="search" size={14} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a resource" aria-label="Find a resource" /></div>
              <div className="topo-zoom inline-flex [border:1px_solid_var(--line-2)] rounded-sm overflow-hidden bg-surface [&_button]:h-[34px] [&_button]:min-w-[38px] [&_button]:p-[0_10px] [&_button]:border-0 [&_button]:bg-none [&_button]:text-muted [&_button]:font-inherit [&_button]:font-semibold [&_button]:cursor-pointer [&_button_+_button]:[border-left:1px_solid_var(--line)] [&_button:hover]:bg-sunken [&_button:hover]:text-text" role="group" aria-label="Zoom">
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
            <EmptyState icon="resources" title="Nothing to draw yet" action={<Link href={envHref(env, "tables")} className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text primary">Create a table</Link>}>
              Define a table's fields and API, relate it to others, and its shape appears here.
            </EmptyState>
          </Card>
        ) : (
          <div className={`topo grid grid-cols-[minmax(0,_1fr)] gap-4 items-start [&.with-detail]:grid-cols-[minmax(0,_1fr)_340px] max-layout:[&.with-detail]:grid-cols-1 ${chosen ? "with-detail" : ""}`}>
            <Card flush className="topo-canvas overflow-hidden">
              <div className="topo-frame overflow-auto max-h-[calc(100vh_-_300px)] min-h-[380px] p-3 bg-sunken [background-image:radial-gradient(var(--line-2)_1px,_transparent_1px)] [background-size:20px_20px] [&_svg]:block" ref={frame} onClick={(e) => e.target === e.currentTarget && setSelected(null)}>
                <svg width={grid.width * zoom} height={grid.height * zoom} viewBox={`0 0 ${grid.width} ${grid.height}`} role="img" aria-label="Diagram of resources and their relations">
                  <defs>
                    <marker id="topo-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" /></marker>
                  </defs>
                  {graph.edges.map((edge) => {
                    const path = route(edge, graph, grid.positions);
                    if (!path) return null;
                    const lit = near?.edgeIds.has(edge.id);
                    return (
                      <g key={edge.id} className={`topo-edge [&_path]:fill-none [&_path]:stroke-faint [&_path]:[stroke-width:1.4] [&_path]:transition-[stroke_0.15s,_opacity_0.18s] [&.schema_path]:[stroke-dasharray:5_4] [&.lit_path]:stroke-brand [&.lit_path]:stroke-2 [&.dim]:opacity-[0.22] [&_text]:[font:650_11px_var(--mono)] [&_text]:fill-brand-strong [&_text]:[paint-order:stroke] [&_text]:stroke-sunken [&_text]:stroke-4 [&_text]:[stroke-linejoin:round] [&.inferred_path]:[stroke-dasharray:2_5] [&.inferred_path]:[stroke-width:1.3] [&.inferred.lit_path]:[stroke-dasharray:2_4] ${edge.kind} ${edge.inferred ? "inferred" : ""} ${lit ? "lit" : near ? "dim" : ""}`}>
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
              <div className="topo-legend flex flex-wrap gap-[8px_22px] items-center p-[10px_16px] [border-top:1px_solid_var(--line)] text-xs text-muted [&_i]:inline-block [&_i]:w-[22px] [&_i]:h-0 [&_i]:mr-2 [&_i]:[border-top:1.5px_solid_var(--faint)] [&_i]:align-middle [&_i.dashed]:border-t-dashed [&_i.dotted]:border-t-dotted [&_i.dotted]:border-t-2">
                <span><i className="solid" />Arrow points at the resource a field refers to</span>
                <span><i className="dashed" />Uses a schema</span>
                {guessed > 0 && <span><i className="dotted" />Guessed from a field name: select a resource to declare it</span>}
                <span>{[...graph.nodes.values()].filter((n) => n.kind === "resource").length} resources · {graph.edges.length} connections</span>
              </div>
            </Card>
            {chosen && <Detail node={chosen} graph={graph} env={env} onClose={() => setSelected(null)} onDeclare={declare} busy={busy} />}
          </div>
        )}
      </Loading>
      {graph && !empty && graph.edges.length === 0 && (
        <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] info" style={{ marginTop: 12 }}>
          No connections yet. Connect resources by adding a <code>relations</code> entry to the resource that holds the key, for example
          {" "}<code>{`{"name": "customer", "resource": "customers", "field": "customer_id"}`}</code> on orders.
        </div>
      )}
      {graph?.dangling.length > 0 && (
        <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] warn" style={{ marginTop: 12 }}>
          {graph.dangling.map((d) => `${d.from}.${d.relation} points at "${d.to}", which does not exist`).join(" · ")}
        </div>
      )}
    </Layout>
  );
}
