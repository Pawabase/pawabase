// Hairline glyphs for the console layout: one pixel, no fills, drawn on a 24 grid. Each entry is a list of shapes:
// "c cx cy r" a circle, "r x y w h rx" a rectangle, anything else a path.
const GLYPHS = {
  home: ["M4 11 12 4l8 7", "M6.5 9.5V19.5h11V9.5", "M10 19.5v-5h4v5"],
  data: ["M4 8 12 4l8 4-8 4z", "m4 12 8 4 8-4", "m4 16 8 4 8-4"],
  files: ["M3.5 7.2V18a1 1 0 0 0 1 1h15a1 1 0 0 0 1-1V9.4a1 1 0 0 0-1-1h-7.6l-2-2.4H4.5a1 1 0 0 0-1 1z"],
  functions: ["M9.5 4.5C7.5 4.5 6.5 5.5 6.5 7.5v2c0 1.3-.8 2.5-2.5 2.5 1.7 0 2.5 1.2 2.5 2.5v2c0 2 1 3 3 3", "M14.5 4.5c2 0 3 1 3 3v2c0 1.3.8 2.5 2.5 2.5-1.7 0-2.5 1.2-2.5 2.5v2c0 2-1 3-3 3"],
  access: ["c 12 8.5 3.5", "M5 20c.9-3.7 3.6-5.6 7-5.6s6.1 1.9 7 5.6"],
  secrets: ["c 8.5 12 3.5", "M11 10.5 20 4", "m17 6.2 2.3 2.2", "m14.2 8.2 1.8 1.7"],
  automation: ["c 5.5 6 2", "c 5.5 18 2", "c 18.5 12 2", "M7.5 6c4.2 0 5.7 2.2 9 5", "M7.5 18c4.2 0 5.7-2.2 9-5"],
  events: ["M4 7h11", "m12.5 4.5 2.5 2.5-2.5 2.5", "M4 12h16", "M4 17h8"],
  realtime: ["c 12 12 1.3", "M8.4 8.4a5.1 5.1 0 0 0 0 7.2", "M15.6 8.4a5.1 5.1 0 0 1 0 7.2", "M5.6 5.6a9 9 0 0 0 0 12.8", "M18.4 5.6a9 9 0 0 1 0 12.8"],
  mail: ["r 3.5 6 17 12 1.5", "m4 7.5 8 6 8-6"],
  monitoring: ["M3 13h4l2.5-6 4.2 11 2.3-5H21"],
  settings: ["M4 7h8", "c 15 7 2.2", "M17.2 7H20", "M4 12h3", "c 9.5 12 2.2", "M11.7 12H20", "M4 17h10", "c 16.5 17 2.2", "M18.7 17H20"],
  check: ["m5 12.5 4.5 4.5L19 7.5"],
  terminal: ["m5 8 4 4-4 4", "M12 16h7"],
  services: ["r 4 4 6 6 1.2", "r 14 4 6 6 1.2", "r 4 14 6 6 1.2", "r 14 14 6 6 1.2"],
  branch: ["c 6 6 2", "c 6 18 2", "c 18 8 2", "M6 8v8", "M18 10c0 3.5-3 4.5-8.5 6.2"],
  search: ["c 11 11 6", "m20 20-4.4-4.4"],
  moon: ["M19.5 14.2A7.8 7.8 0 0 1 9.8 4.5a7.8 7.8 0 1 0 9.7 9.7z"],
  sun: ["c 12 12 3.8", "M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"],
  panel: ["r 3.5 4.5 17 15 2", "M9.5 4.5v15"],
  chevron: ["m6.5 9.5 5.5 5.5 5.5-5.5"],
  star: ["M12 4.2l2.3 4.9 5.3.7-3.9 3.7 1 5.3-4.7-2.6-4.7 2.6 1-5.3-3.9-3.7 5.3-.7z"],
  plus: ["M12 5v14M5 12h14"],
};

function shape(spec, index) {
  const parts = spec.split(" ");
  if (parts[0] === "c" && parts.length === 4) return <circle key={index} cx={parts[1]} cy={parts[2]} r={parts[3]} />;
  if (parts[0] === "r" && parts.length >= 5) return <rect key={index} x={parts[1]} y={parts[2]} width={parts[3]} height={parts[4]} rx={parts[5] || 0} />;
  return <path key={index} d={spec} />;
}

/** A one-pixel line glyph. Always the same weight, whatever its size. */
export default function Hair({ name, size = 20, className = "" }) {
  const shapes = GLYPHS[name] || GLYPHS.services;
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} className={`hair ${className}`} fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {shapes.map(shape)}
    </svg>
  );
}
