import { arrange, isCramped, GAP_X, DEFAULT_SIZE } from "../src/lib/flowLayout.js";
let failures = 0;
const check = (ok, label) => { if (!ok) { failures += 1; console.log("FAIL", label); } };
const nodes = (n) => Array.from({ length: n }, (_, i) => ({ id: `n${i}`, position: { x: 0, y: 0 } }));
const boxes = (ns, p, sizes = {}) => ns.map((n) => ({ id: n.id, ...DEFAULT_SIZE, ...(sizes[n.id] || {}), ...p[n.id] }));
const overlap = (b) => { for (let i = 0; i < b.length; i++) for (let j = i + 1; j < b.length; j++) { const a = b[i], c = b[j]; if (a.x < c.x + c.width && c.x < a.x + a.width && a.y < c.y + c.height && c.y < a.y + a.height) return [a.id, c.id]; } return null; };

// the flows Cloud makes: a chain, every block at 0,0
let ns = nodes(4); let es = [{ source: "n0", target: "n1" }, { source: "n1", target: "n2" }, { source: "n2", target: "n3" }];
check(isCramped(ns), "cramped detected");
let p = arrange(ns, es);
check(!overlap(boxes(ns, p)), "chain: no overlap");
check(p.n0.y < p.n1.y && p.n1.y < p.n2.y && p.n2.y < p.n3.y, "chain: top to bottom");
check(new Set(Object.values(p).map((q) => q.x)).size === 1, "chain: one column");
check(!isCramped(ns.map((n) => ({ ...n, position: p[n.id] }))), "arranged is not cramped");

// a branch keeps its handle order: if -> (true: a, false: b) -> merge
ns = nodes(5).map((n, i) => ({ ...n, id: ["t", "if", "a", "b", "m"][i] }));
es = [{ source: "t", target: "if" }, { source: "if", target: "b", sourceHandle: "false" }, { source: "if", target: "a", sourceHandle: "true" }, { source: "a", target: "m" }, { source: "b", target: "m" }];
p = arrange(ns, es, {}, (id) => (id === "if" ? ["true", "false", "error"] : ["next"]));
check(p.a.x < p.b.x, "branch: true left of false");
check(p.if.y < p.a.y && p.a.y < p.m.y, "branch: layers");
check(!overlap(boxes(ns, p)), "branch: no overlap");
const mid = (id) => p[id].x + DEFAULT_SIZE.width / 2;
check(Math.abs(mid("if") - (mid("a") + mid("b")) / 2) < 2, "branch: parent centred over its children");

// a loop does not stack the blocks
ns = nodes(3); es = [{ source: "n0", target: "n1" }, { source: "n1", target: "n2" }, { source: "n2", target: "n1" }];
p = arrange(ns, es); check(!overlap(boxes(ns, p)) && p.n0.y < p.n1.y && p.n1.y < p.n2.y, "loop handled");

// separate groups sit side by side
ns = nodes(4); es = [{ source: "n0", target: "n1" }, { source: "n2", target: "n3" }];
p = arrange(ns, es); check(!overlap(boxes(ns, p)), "groups: no overlap"); check(p.n2.x >= p.n0.x + DEFAULT_SIZE.width + GAP_X, "groups: apart");

// wide fan-out and varied sizes, random graphs: never overlap, forward edges go down
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
for (let round = 0; round < 300; round++) {
  const n = 1 + Math.floor(rnd() * 24); ns = nodes(n); es = [];
  for (let k = 0; k < n * 1.4; k++) { const s = Math.floor(rnd() * n), t = Math.floor(rnd() * n); if (s !== t) es.push({ source: `n${s}`, target: `n${t}`, sourceHandle: ["next", "true", "false"][Math.floor(rnd() * 3)] }); }
  const sizes = Object.fromEntries(ns.map((x) => [x.id, { width: 170 + Math.floor(rnd() * 90), height: 90 + Math.floor(rnd() * 60) }]));
  p = arrange(ns, es, sizes, () => ["next", "true", "false"]);
  const o = overlap(boxes(ns, p, sizes)); if (o) { check(false, `random ${round}: overlap ${o}`); break; }
  check(ns.every((x) => Number.isFinite(p[x.id].x) && Number.isFinite(p[x.id].y)), "finite");
}
check(Object.keys(arrange([], [])).length === 0, "empty");
console.log(failures ? `${failures} failures` : "layout checks passed");
