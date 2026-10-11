// The shape of an environment's data: resources as tables, the relations between them, and the schemas they embed.
// Pure functions (no React), so the graph and its layout can be reasoned about, and tested, on their own.

export const CARD = { width: 236, head: 40, row: 22, rows: 7, pad: 10, gapX: 120, gapY: 32, margin: 28 };

/** @returns {{nodes: Map<string, object>, edges: object[], dangling: object[]}} */
export function buildGraph(resources = [], schemas = []) {
  const nodes = new Map();
  for (const spec of resources) nodes.set(`r:${spec.name}`, { id: `r:${spec.name}`, kind: "resource", name: spec.name, spec, fields: spec.fields || [] });
  for (const spec of schemas) nodes.set(`s:${spec.name}`, { id: `s:${spec.name}`, kind: "schema", name: spec.name, spec, fields: spec.fields || [] });

  const edges = [];
  const dangling = [];
  const seen = new Set();
  const link = (edge) => {
    const key = `${edge.kind}|${edge.from}|${edge.field}|${edge.to}`;
    if (seen.has(key)) return;
    seen.add(key);
    edges.push({ ...edge, id: key });
  };

  for (const spec of resources) {
    for (const relation of spec.relations || []) {
      const target = `r:${relation.resource}`;
      if (!nodes.has(target)) {
        dangling.push({ from: spec.name, relation: relation.name, to: relation.resource });
        continue;
      }
      // belongs_to: the key is on this resource. has_many: the key is on the other one, pointing back here.
      if ((relation.type || "belongs_to") === "belongs_to") {
        link({ kind: "relation", from: `r:${spec.name}`, to: target, field: relation.field, name: relation.name, many: false });
      } else {
        link({ kind: "relation", from: target, to: `r:${spec.name}`, field: relation.field, name: relation.name, many: true });
      }
    }
    for (const field of spec.fields || []) {
      if (field.type === "ref" && field.schema && nodes.has(`s:${field.schema}`)) {
        link({ kind: "schema", from: `r:${spec.name}`, to: `s:${field.schema}`, field: field.name, name: field.schema });
      }
    }
  }
  // Many resources are connected only by naming: `customer_id` on orders, with a `customers` resource beside it. Declared relations are
  // what the API acts on, so these are drawn as guesses (dotted), and can be declared from the detail panel.
  const names = [...nodes.values()].filter((n) => n.kind === "resource");
  for (const node of names) {
    for (const field of node.fields) {
      const match = /^(.+?)_?id$/i.exec(field.name || "");
      if (!match || field.name === "id" || field.type === "ref" || ["array", "object", "json", "boolean"].includes(field.type)) continue;
      const stem = match[1].toLowerCase();
      const target = names.find((other) => other.id !== node.id && [other.name, singular(other.name), other.spec.table || ""].some((c) => c && c.toLowerCase() === stem));
      if (!target) continue;
      link({ kind: "relation", from: node.id, to: target.id, field: field.name, name: match[1], many: false, inferred: true });
    }
  }
  return { nodes, edges, dangling };
}

/** categories -> category, orders -> order, boxes -> box. Enough to read a field name; not a language library. */
export function singular(word) {
  const w = String(word);
  if (/ies$/i.test(w)) return `${w.slice(0, -3)}y`;
  if (/(ses|xes|zes|ches|shes)$/i.test(w)) return w.slice(0, -2);
  if (/s$/i.test(w) && !/ss$/i.test(w)) return w.slice(0, -1);
  return w;
}

export function cardHeight(node) {
  const shown = Math.min(node.fields.length, CARD.rows);
  const extra = node.fields.length > CARD.rows ? CARD.row : 0;
  return CARD.head + Math.max(shown, 1) * CARD.row + extra + CARD.pad;
}

/** Parents sit to the left of the resources that point at them; schemas get the last column. */
export function layout(graph) {
  const { nodes, edges } = graph;
  const parentsOf = new Map();
  for (const edge of edges) {
    if (edge.kind !== "relation" || edge.from === edge.to) continue;
    if (!parentsOf.has(edge.from)) parentsOf.set(edge.from, new Set());
    parentsOf.get(edge.from).add(edge.to);
  }
  const depth = new Map();
  const visit = (id, trail = new Set()) => {
    if (depth.has(id)) return depth.get(id);
    if (trail.has(id)) return 0; // a cycle: break it here
    trail.add(id);
    let d = 0;
    for (const parent of parentsOf.get(id) || []) d = Math.max(d, visit(parent, trail) + 1);
    trail.delete(id);
    depth.set(id, d);
    return d;
  };
  const resources = [...nodes.values()].filter((n) => n.kind === "resource");
  const schemas = [...nodes.values()].filter((n) => n.kind === "schema");
  resources.forEach((n) => visit(n.id));
  const deepest = resources.reduce((m, n) => Math.max(m, depth.get(n.id) || 0), 0);
  const used = new Set(edges.filter((e) => e.kind === "schema").map((e) => e.to));
  const columns = [];
  for (const n of resources) (columns[depth.get(n.id)] ||= []).push(n);
  const schemaColumn = schemas.filter((s) => used.has(s.id));
  const orphanSchemas = schemas.filter((s) => !used.has(s.id));
  if (schemaColumn.length) columns[deepest + 1] = schemaColumn;
  const cols = columns.map((c) => c || []);

  // Order each column by where its parents sit, so related cards line up and lines cross less.
  const order = new Map();
  cols[0]?.sort((a, b) => a.name.localeCompare(b.name)).forEach((n, i) => order.set(n.id, i));
  for (let c = 1; c < cols.length; c += 1) {
    const score = (n) => {
      const ps = [...(parentsOf.get(n.id) || [])].filter((p) => order.has(p));
      const targets = edges.filter((e) => e.kind === "schema" && e.to === n.id).map((e) => e.from).filter((p) => order.has(p));
      const all = ps.length ? ps : targets;
      return all.length ? all.reduce((s, p) => s + order.get(p), 0) / all.length : 1e6;
    };
    cols[c].sort((a, b) => score(a) - score(b) || a.name.localeCompare(b.name)).forEach((n, i) => order.set(n.id, i));
  }

  const positions = new Map();
  let width = 0;
  let height = 0;
  cols.forEach((column, c) => {
    let y = CARD.margin;
    const x = CARD.margin + c * (CARD.width + CARD.gapX);
    for (const node of column) {
      const h = cardHeight(node);
      positions.set(node.id, { x, y, w: CARD.width, h });
      y += h + CARD.gapY;
    }
    width = Math.max(width, x + CARD.width + CARD.margin);
    height = Math.max(height, y);
  });
  // Schemas nothing uses sit in a row under everything else.
  let x = CARD.margin;
  const rowY = height + 8;
  let rowHeight = 0;
  for (const node of orphanSchemas) {
    const h = cardHeight(node);
    positions.set(node.id, { x, y: rowY, w: CARD.width, h });
    x += CARD.width + 24;
    rowHeight = Math.max(rowHeight, h);
    width = Math.max(width, x);
  }
  if (rowHeight) height = rowY + rowHeight + CARD.margin;
  return { positions, width: Math.max(width, 400), height: Math.max(height, 240), columns: cols.length };
}

/** Where a line leaves and arrives: from the parent's header to the row of the key that points at it. */
export function route(edge, graph, positions) {
  const from = positions.get(edge.from);
  const to = positions.get(edge.to);
  if (!from || !to) return null;
  const child = graph.nodes.get(edge.from);
  const index = child.fields.findIndex((f) => f.name === edge.field);
  const rowY = index >= 0 && index < CARD.rows ? from.y + CARD.head + index * CARD.row + CARD.row / 2 : from.y + CARD.head / 2;
  const headY = to.y + CARD.head / 2;
  if (edge.kind === "schema") {
    // A field of a resource that holds a schema: the line leaves the field's row and arrives at the schema's header.
    if (from.x + from.w + 30 <= to.x) {
      const x1 = from.x + from.w;
      const dx = Math.max(40, (to.x - x1) / 2);
      return { d: `M${x1} ${rowY} C${x1 + dx} ${rowY}, ${to.x - dx} ${headY}, ${to.x} ${headY}`, x: (x1 + to.x) / 2, y: (rowY + headY) / 2, start: [x1, rowY], end: [to.x, headY] };
    }
    const x1 = from.x + from.w;
    const out = Math.max(x1, to.x + to.w) + 46;
    return { d: `M${x1} ${rowY} C${out} ${rowY}, ${out} ${headY}, ${to.x + to.w} ${headY}`, x: out - 8, y: (rowY + headY) / 2, start: [x1, rowY], end: [to.x + to.w, headY] };
  }
  // The key is on `edge.from`; the line is drawn parent (edge.to) -> child (edge.from).
  if (to.x + to.w + 30 <= from.x) {
    const x1 = to.x + to.w;
    const x2 = from.x;
    const dx = Math.max(40, (x2 - x1) / 2);
    return { d: `M${x1} ${headY} C${x1 + dx} ${headY}, ${x2 - dx} ${rowY}, ${x2} ${rowY}`, x: (x1 + x2) / 2, y: (headY + rowY) / 2, start: [x1, headY], end: [x2, rowY] };
  }
  // Same column, or pointing backwards, or a table that refers to itself: loop around the right-hand side.
  const x1 = to.x + to.w;
  const x2 = from.x + from.w;
  const out = Math.max(x1, x2) + 46;
  return { d: `M${x1} ${headY} C${out} ${headY}, ${out} ${rowY}, ${x2} ${rowY}`, x: out - 8, y: (headY + rowY) / 2, start: [x1, headY], end: [x2, rowY] };
}

export function neighbours(graph, id) {
  const ids = new Set([id]);
  const edgeIds = new Set();
  for (const edge of graph.edges) {
    if (edge.from === id || edge.to === id) {
      ids.add(edge.from);
      ids.add(edge.to);
      edgeIds.add(edge.id);
    }
  }
  return { ids, edgeIds };
}
