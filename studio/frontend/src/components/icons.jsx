// A small line-icon set on a 24-unit grid. Each icon is a list of shapes:
// "c cx cy r" a circle, "r x y w h rx" a rect, "e cx cy rx ry" an ellipse,
// anything else a path.

const SHAPES = {
  overview: ["M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"],
  database: ["e 12 5 8 3", "M4 5v14c0 1.66 3.6 3 8 3s8-1.34 8-3V5", "M4 12c0 1.66 3.6 3 8 3s8-1.34 8-3"],
  resources: ["r 3 3 18 18 3", "M3 9h18M3 15h18M9 9v12"],
  schemas: ["M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1", "M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"],
  transformers: ["M16 3l4 4-4 4", "M20 7H4", "M8 21l-4-4 4-4", "M4 17h16"],
  policies: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z", "m9 12 2 2 4-4"],
  routes: ["c 6 18 3", "c 18 6 3", "M6 3v12", "M18 9a9 9 0 0 1-9 9"],
  gitBranch: ["c 6 5 2", "c 18 19 2", "c 18 5 2", "M8 5h2a4 4 0 0 1 4 4v6a4 4 0 0 0 4 4", "M14 9a4 4 0 0 0 4-4"],
  explorer: ["c 12 12 9", "m16 8-2.5 5.5L8 16l2.5-5.5z", "c 12 12 1"],
  functions: ["m16 18 6-6-6-6", "m8 6-6 6 6 6", "M14 4l-4 16"],
  flows: ["r 3 3 7 7 2", "r 14 14 7 7 2", "M10 6.5h4.5a3 3 0 0 1 3 3V14"],
  subscriptions: ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  schedules: ["c 12 12 9", "M12 7v5l3 2"],
  webhooks: ["M22 2 11 13", "M22 2 15 22l-4-9-9-4 20-7z"],
  "inbound-hooks": ["M22 12h-6l-2 3h-4l-2-3H2", "M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"],
  "mail-templates": ["r 2 4 20 16 3", "m22 7-10 6L2 7"],
  users: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "c 9 7 4", "M22 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  storage: ["M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z"],
  realtime: ["M4.9 19.1C1 15.2 1 8.8 4.9 4.9", "M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5", "M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5", "M19.1 4.9C23 8.8 23 15.1 19.1 19", "c 12 12 2"],
  jobs: ["m12 2 10 5-10 5L2 7z", "m2 17 10 5 10-5", "m2 12 10 5 10-5"],
  events: ["M22 12h-4l-3 9L9 3l-3 9H2"],
  observability: ["M3 3v18h18", "M18 17V9", "M13 17V5", "M8 17v-3"],
  backups: ["M12 3v12", "m7 10 5 5 5-5", "M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"],
  releases: ["m12 2 9 5-9 5-9-5z", "m3 12 9 5 9-5", "m3 17 9 5 9-5"],
  keys: ["c 7.5 15.5 5.5", "m21 2-9.6 9.6", "m15.5 7.5 3 3L22 7l-3-3"],
  secrets: ["r 3 11 18 11 3", "M7 11V7a5 5 0 0 1 10 0v4"],
  settings: ["M4 21v-7", "M4 10V3", "M12 21v-9", "M12 8V3", "M20 21v-5", "M20 12V3", "M1 14h6", "M9 8h6", "M17 16h6"],
  org: ["M3 21h18", "M5 21V7l7-4 7 4v14", "M9 9h.01M9 13h.01M9 17h.01M15 9h.01M15 13h.01M15 17h.01"],
  team: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "c 9 7 4", "M22 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  mail: ["r 2 4 20 16 2", "m22 7-10 6L2 7"],
  projects: ["r 3 3 7 7 2", "r 14 3 7 7 2", "r 3 14 7 7 2", "r 14 14 7 7 2"],
  audit: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M16 13H8", "M16 17H8"],
  buckets: ["M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z"],
  plus: ["M12 5v14", "M5 12h14"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  trash: ["M3 6h18", "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6", "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"],
  chevronDown: ["m6 9 6 6 6-6"],
  chevronUp: ["m18 15-6-6-6 6"],
  chevronRight: ["m9 18 6-6-6-6"],
  sun: ["c 12 12 4", "M12 2v2", "M12 20v2", "m4.93 4.93 1.41 1.41", "m17.66 17.66 1.41 1.41", "M2 12h2", "M20 12h2", "m6.34 17.66-1.41 1.41", "m19.07 4.93-1.41 1.41"],
  moon: ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "m16 17 5-5-5-5", "M21 12H9"],
  external: ["M15 3h6v6", "M10 14 21 3", "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"],
  code: ["m16 18 6-6-6-6", "m8 6-6 6 6 6"],
  send: ["m22 2-7 20-4-9-9-4 20-7z", "M22 2 11 13"],
  clock: ["c 12 12 10", "M12 6v6l4 2"],
  upload: ["M12 16V4", "m7 9 5-5 5 5", "M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"],
  copy: ["r 9 9 13 13 2", "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"],
  check: ["M20 6 9 17l-5-5"],
  menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  bolt: ["M13 2 3 14h9l-1 8 10-12h-9l1-8z"],
  layers: ["m12 2 10 5-10 5L2 7z", "m2 17 10 5 10-5", "m2 12 10 5 10-5"],
  play: ["m6 3 14 9-14 9z"],
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "c 12 12 3"],
  braces: ["M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1", "M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"],
  link: ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
  search: ["c 11 11 7", "m20 20-3.5-3.5"],
  arrowUp: ["M12 19V5", "m5 12 7-7 7 7"],
  arrowDown: ["M12 5v14", "m19 12-7 7-7-7"],
  pulse: ["M3 12h4l3-8 4 16 3-8h4"],
  enter: ["M20 4v7a4 4 0 0 1-4 4H4", "m9 10-5 5 5 5"],
  sparkle: ["M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"],
};

function shape(spec, i) {
  const [kind, ...n] = spec.split(" ");
  if (kind === "c") return <circle key={i} cx={n[0]} cy={n[1]} r={n[2]} />;
  if (kind === "r") return <rect key={i} x={n[0]} y={n[1]} width={n[2]} height={n[3]} rx={n[4] || 0} />;
  if (kind === "e") return <ellipse key={i} cx={n[0]} cy={n[1]} rx={n[2]} ry={n[3]} />;
  return <path key={i} d={spec} />;
}

export function Icon({ name, size, className = "", style }) {
  const shapes = SHAPES[name] || SHAPES.layers;
  return (
    <svg
      viewBox="0 0 24 24"
      className={`icon ${className}`}
      style={size ? { width: size, height: size, ...style } : style}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {shapes.map(shape)}
    </svg>
  );
}
