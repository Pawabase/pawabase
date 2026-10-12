// Hairline scenes: one per service, drawn on a 200 x 120 grid, one pixel, no fills. `a` marks the single accent stroke in a scene.
const SCENES = {
  data: (
    <>
      <ellipse cx="100" cy="30" rx="62" ry="15" />
      <path d="M38 30v22c0 8 28 15 62 15s62-7 62-15V30" />
      <path d="M38 52v22c0 8 28 15 62 15s62-7 62-15V52" />
      <path d="M38 74v16c0 8 28 15 62 15s62-7 62-15V74" />
      <path className="a" d="M70 28h22M104 32h26M84 24h16" />
      <circle className="a" cx="146" cy="24" r="2.5" />
    </>
  ),
  files: (
    <>
      <path d="M26 40v54a4 4 0 0 0 4 4h140a4 4 0 0 0 4-4V50a4 4 0 0 0-4-4H98l-8-10H30a4 4 0 0 0-4 4z" />
      <path d="M52 46V28l34-4 6 22" />
      <path d="M70 46V20l40-2 4 28" />
      <path className="a" d="M120 46l10-22 26 6-6 16" />
      <path d="M40 66h120M40 80h80" strokeDasharray="2 5" />
    </>
  ),
  functions: (
    <>
      <path d="M78 24c-12 0-18 6-18 18v8c0 7-4 10-12 10 8 0 12 3 12 10v8c0 12 6 18 18 18" />
      <path d="M122 24c12 0 18 6 18 18v8c0 7 4 10 12 10-8 0-12 3-12 10v8c0 12-6 18-18 18" />
      <path className="a" d="M92 70l16-10M92 54l16 10" />
      <circle cx="100" cy="62" r="4" />
      <path d="M100 18v14M100 92v14" strokeDasharray="2 4" />
    </>
  ),
  access: (
    <>
      <path d="M100 14l44 14v30c0 28-20 44-44 52-24-8-44-24-44-52V28z" />
      <circle className="a" cx="100" cy="52" r="11" />
      <path d="M78 86c4-12 12-18 22-18s18 6 22 18" />
      <path d="M100 63v10" strokeDasharray="2 3" />
    </>
  ),
  secrets: (
    <>
      <circle cx="68" cy="60" r="22" />
      <circle className="a" cx="68" cy="60" r="7" />
      <path d="M90 60h78M140 60v16M156 60v10" />
      <path d="M30 100h140" strokeDasharray="2 5" />
      <rect x="120" y="86" width="36" height="22" rx="4" />
      <path d="M128 86v-6a10 10 0 0 1 20 0v6" />
    </>
  ),
  automation: (
    <>
      <circle cx="30" cy="60" r="10" />
      <circle cx="90" cy="26" r="10" />
      <circle cx="90" cy="94" r="10" />
      <circle className="a" cx="150" cy="60" r="12" />
      <path d="M40 58c20 0 28-24 40-30M40 62c20 0 28 24 40 30" />
      <path className="a" d="M100 28c24 2 30 18 40 28M100 92c24-2 30-18 40-28" />
      <path d="M166 60h20" strokeDasharray="2 4" />
    </>
  ),
  events: (
    <>
      <path d="M10 60h180" />
      <path d="M30 60V44M46 60V30M62 60V50M78 60V22M94 60V52M110 60V38M126 60V54M142 60V28M158 60V46M174 60V54" />
      <path d="M30 60v10M46 60v18M62 60v8M78 60v22M94 60v8M110 60v14M126 60v6M142 60v20M158 60v10" />
      <circle className="a" cx="78" cy="22" r="3" />
      <circle className="a" cx="142" cy="28" r="3" />
      <path className="a" d="M78 22l64 6" strokeDasharray="2 4" />
    </>
  ),
  realtime: (
    <>
      <circle className="a" cx="100" cy="60" r="5" />
      <path d="M78 42a30 30 0 0 0 0 36M122 42a30 30 0 0 1 0 36" />
      <path d="M62 30a52 52 0 0 0 0 60M138 30a52 52 0 0 1 0 60" />
      <path d="M46 18a74 74 0 0 0 0 84M154 18a74 74 0 0 1 0 84" strokeDasharray="2 5" />
    </>
  ),
  mail: (
    <>
      <rect x="34" y="34" width="132" height="76" rx="6" />
      <path d="M34 42l66 44 66-44" />
      <path className="a" d="M120 18c16 0 26 6 34 16" />
      <path className="a" d="M148 26l8 8-12 3" />
      <path d="M50 104l36-30M150 104l-36-30" strokeDasharray="2 4" />
    </>
  ),
  monitoring: (
    <>
      <path d="M24 16v88h156" />
      <path d="M24 70h156M24 44h156" strokeDasharray="2 5" />
      <path d="M34 84l24-18 20 8 24-34 24 20 22-28 22 14" />
      <circle className="a" cx="126" cy="46" r="4" />
      <path className="a" d="M126 50v40" strokeDasharray="2 3" />
    </>
  ),
  settings: (
    <>
      <path d="M26 30h148M26 60h148M26 90h148" />
      <circle cx="62" cy="30" r="8" />
      <circle className="a" cx="128" cy="60" r="8" />
      <circle cx="90" cy="90" r="8" />
      <path d="M26 30h28M70 30h104M26 60h94M136 60h38M26 90h56M98 90h76" stroke="none" />
    </>
  ),
};

/** A hairline scene for a service or topic. Colour comes from the text colour; the accent from the brand. */
export default function Art({ name, className = "" }) {
  return (
    <svg viewBox="0 0 200 120" className={`art ${className}`} fill="none" aria-hidden>
      {SCENES[name] || SCENES.data}
    </svg>
  );
}
