// Arranging a flow so it reads top to bottom: triggers at the top, each block below what feeds it, branches side by side in the order of
// their handles, and nothing on top of anything else.
//
// The steps are the usual layered ones. Loops (an edge that points back up) are set aside so they do not pull the picture apart; blocks go in
// layers by the longest path from a trigger; each layer is ordered to keep the lines from crossing; then blocks are placed under the middle of
// what they connect to, with room between neighbours. Separate groups of blocks sit side by side. It is pure, so it can be checked on its own.

export const GAP_X = 64;
export const GAP_Y = 88;
export const DEFAULT_SIZE = { width: 220, height: 116 };

const size = (sizes, id) => ({ ...DEFAULT_SIZE, ...(sizes?.[id] || {}) });

/** Whether blocks sit on top of each other (a flow made by code has every block at 0, 0). */
export function isCramped(nodes, sizes) {
  const boxes = nodes.map((n) => ({ ...size(sizes, n.id), x: n.position?.x ?? 0, y: n.position?.y ?? 0 }));
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a = boxes[i], b = boxes[j];
      if (a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height) return true;
    }
  }
  return false;
}

/**
 * Positions (top left, by id) for *nodes* joined by *edges* (``{source, target, sourceHandle}``).
 *
 * @param sizes   ``{id: {width, height}}`` as measured on screen; anything missing uses a typical block's size.
 * @param handles ``(id) => [handle names]`` in the order they appear left to right, so branches keep their order.
 */
export function arrange(nodes, edges, sizes = {}, handles = () => []) {
  const ids = nodes.map((n) => n.id);
  if (!ids.length) return {};
  const index = new Map(ids.map((id, i) => [id, i]));
  const wanted = (edge) => index.has(edge.source) && index.has(edge.target) && edge.source !== edge.target;

  // Outgoing links per block, branches in handle order.
  const out = new Map(ids.map((id) => [id, []]));
  const seen = new Set();
  for (const edge of edges.filter(wanted)) {
    const key = `${edge.source}>${edge.target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const order = handles(edge.source);
    const rank = order.indexOf(edge.sourceHandle || "next");
    out.get(edge.source).push({ to: edge.target, rank: rank < 0 ? order.length : rank, at: out.get(edge.source).length });
  }
  for (const list of out.values()) list.sort((a, b) => a.rank - b.rank || a.at - b.at);

  const incoming = new Map(ids.map((id) => [id, 0]));
  for (const list of out.values()) for (const { to } of list) incoming.set(to, incoming.get(to) + 1);

  // Walk from the blocks nothing feeds (or, in a closed loop, the first), setting aside any edge that returns to a block still being walked.
  const back = new Set();
  const state = new Map();
  const preorder = [];
  const walk = (start) => {
    const stack = [[start, 0]];
    state.set(start, 1);
    preorder.push(start);
    while (stack.length) {
      const top = stack[stack.length - 1];
      const [id, at] = top;
      const next = out.get(id)[at];
      if (!next) {
        state.set(id, 2);
        stack.pop();
        continue;
      }
      top[1] += 1;
      if (state.get(next.to) === 1) back.add(`${id}>${next.to}`);
      else if (!state.has(next.to)) {
        state.set(next.to, 1);
        preorder.push(next.to);
        stack.push([next.to, 0]);
      }
    }
  };
  for (const id of ids) if (!incoming.get(id) && !state.has(id)) walk(id);
  for (const id of ids) if (!state.has(id)) walk(id);

  const forward = new Map(ids.map((id) => [id, out.get(id).filter(({ to }) => !back.has(`${id}>${to}`)).map(({ to }) => to)]));
  const parents = new Map(ids.map((id) => [id, []]));
  for (const [id, list] of forward) for (const to of list) parents.get(to).push(id);

  // Layers: the longest path from a block with nothing above it.
  const layer = new Map();
  const depth = (id) => {
    if (layer.has(id)) return layer.get(id);
    layer.set(id, 0); // guards a cycle the walk did not catch
    const value = parents.get(id).reduce((deepest, parent) => Math.max(deepest, depth(parent) + 1), 0);
    layer.set(id, value);
    return value;
  };
  ids.forEach(depth);

  // Separate groups of connected blocks are laid out apart and set side by side.
  const group = new Map();
  const find = (id) => {
    while (group.get(id) !== id) {
      group.set(id, group.get(group.get(id)));
      id = group.get(id);
    }
    return id;
  };
  ids.forEach((id) => group.set(id, id));
  for (const [id, list] of forward) for (const to of list) group.set(find(id), find(to));
  const groups = new Map();
  for (const id of preorder) {
    const root = find(id);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(id);
  }

  const positions = {};
  let left = 0;
  for (const members of groups.values()) {
    const placed = place(members, layer, forward, parents, sizes);
    const right = Math.max(...members.map((id) => placed[id].x + size(sizes, id).width));
    for (const id of members) positions[id] = { x: Math.round(placed[id].x + left), y: Math.round(placed[id].y) };
    left += right + GAP_X * 1.5;
  }
  return positions;
}

function place(members, layer, forward, parents, sizes) {
  const levels = [];
  for (const id of members) (levels[layer.get(id)] ||= []).push(id); // members arrive in walk order, so branches start in handle order
  const rows = levels.filter(Boolean);
  const at = new Map();
  const reindex = () => rows.forEach((row) => row.forEach((id, i) => at.set(id, i)));
  reindex();

  // Fewer crossings: order each row by the average place of what it connects to, sweeping down and up. Ties keep their order.
  const mean = (list) => list.reduce((sum, id) => sum + at.get(id), 0) / list.length;
  const sweep = (down) => {
    for (const row of down ? rows.slice(1) : rows.slice(0, -1).reverse()) {
      const keyed = row.map((id, i) => {
        const near = (down ? parents : forward).get(id).filter((other) => at.has(other));
        return { id, key: near.length ? mean(near) : i, i };
      });
      keyed.sort((a, b) => a.key - b.key || a.i - b.i);
      keyed.forEach(({ id }, i) => { row[i] = id; at.set(id, i); });
    }
  };
  for (let pass = 0; pass < 4; pass += 1) {
    sweep(true);
    sweep(false);
  }

  // Rows from the top, as tall as their tallest block.
  const y = new Map();
  let top = 0;
  for (const row of rows) {
    row.forEach((id) => y.set(id, top));
    top += Math.max(...row.map((id) => size(sizes, id).height)) + GAP_Y;
  }

  // Across: start packed and centred, then let every block drift to the middle of its neighbours without letting two touch.
  const centre = new Map();
  for (const row of rows) {
    const total = row.reduce((sum, id) => sum + size(sizes, id).width, 0) + GAP_X * (row.length - 1);
    let x = -total / 2;
    for (const id of row) {
      const w = size(sizes, id).width;
      centre.set(id, x + w / 2);
      x += w + GAP_X;
    }
  }
  const apart = (row) => {
    const w = row.map((id) => size(sizes, id).width);
    const want = row.map((id) => centre.get(id));
    const forwardPass = [...want];
    for (let i = 1; i < row.length; i += 1) forwardPass[i] = Math.max(want[i], forwardPass[i - 1] + (w[i - 1] + w[i]) / 2 + GAP_X);
    const backPass = [...want];
    for (let i = row.length - 2; i >= 0; i -= 1) backPass[i] = Math.min(want[i], backPass[i + 1] - (w[i] + w[i + 1]) / 2 - GAP_X);
    // Both satisfy every gap, so their average does too.
    row.forEach((id, i) => centre.set(id, (forwardPass[i] + backPass[i]) / 2));
  };
  const drift = (down) => {
    for (const row of down ? rows.slice(1) : rows.slice(0, -1).reverse()) {
      for (const id of row) {
        const near = (down ? parents : forward).get(id);
        if (near.length) centre.set(id, near.reduce((sum, other) => sum + centre.get(other), 0) / near.length);
      }
      apart(row);
    }
  };
  for (let pass = 0; pass < 6; pass += 1) {
    drift(true);
    drift(false);
  }
  drift(true);

  const min = Math.min(...members.map((id) => centre.get(id) - size(sizes, id).width / 2));
  return Object.fromEntries(members.map((id) => [id, { x: centre.get(id) - size(sizes, id).width / 2 - min, y: y.get(id) }]));
}
