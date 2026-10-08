// Small presentational pieces shared by every Observability section.

const TONES = { mint: "ok", rose: "bad", peach: "warn", butter: "warn", sky: "info", lavender: "", ok: "ok", bad: "bad", warn: "warn" };

/** One row of headline numbers. Children are <Stat/>. */
export function StatStrip({ children }) {
  return <div className="statstrip">{children}</div>;
}

export function Stat({ tone, value, label }) {
  return (
    <div>
      <span className="v">{value}</span>
      <span className="l"><i className={`dot ${TONES[tone] ?? ""}`} />{label}</span>
    </div>
  );
}

/** A tiny trend line. `values` is a plain array of numbers. */
export function Spark({ values, color = "var(--brand)" }) {
  const max = Math.max(...values, 1);
  const n = Math.max(values.length - 1, 1);
  const points = values.map((v, i) => [(i / n) * 100, 28 - (v / max) * 24]);
  const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
      {points.length > 1 && <polygon points={`0,30 ${line} 100,30`} fill={color} opacity="0.12" />}
      {points.length > 1 && <polyline points={line} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />}
    </svg>
  );
}

export function Kpi({ label, value, sub, bad, values, color, active, onClick }) {
  return (
    <button type="button" className={`kpi ${active ? "active" : ""}`} onClick={onClick} aria-pressed={active}>
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      <span className={`sub ${bad ? "bad" : ""}`}>{sub}</span>
      <Spark values={values} color={color} />
    </button>
  );
}
