// Small presentational pieces shared by every Observability section.

const TONES = { mint: "ok", rose: "bad", peach: "warn", butter: "warn", sky: "info", lavender: "", ok: "ok", bad: "bad", warn: "warn" };

/** One row of headline numbers. Children are <Stat/>. */
export function StatStrip({ children }) {
  return <div className="statstrip flex flex-wrap items-baseline gap-[4px_0] p-0 m-[0_0_14px] bg-none rounded-none overflow-visible [&_>_div]:inline-flex [&_>_div]:items-baseline [&_>_div]:gap-[7px] [&_>_div]:p-[0_18px] [&_>_div]:m-0 [&_>_div]:border-0 [&_>_div]:bg-none [&_>_div]:[border-left:1px_solid_var(--line-2)] [&_>_div:first-child]:pl-0 [&_>_div:first-child]:border-l-0 [&_.v]:text-heading [&_.v]:font-bold [&_.v]:tracking-[-0.02em] [&_.v]:tabular-nums [&_b]:text-heading [&_b]:font-bold [&_b]:tracking-[-0.02em] [&_b]:tabular-nums [&_.l]:inline-flex [&_.l]:items-center [&_.l]:gap-1.5 [&_.l]:text-muted [&_.l]:text-ui [&_.l]:font-medium [&_.l]:normal-case [&_span:not(.v)]:inline-flex [&_span:not(.v)]:items-center [&_span:not(.v)]:gap-1.5 [&_span:not(.v)]:text-muted [&_span:not(.v)]:text-ui [&_span:not(.v)]:font-medium [&_span:not(.v)]:normal-case [&_.dot]:w-1.5 [&_.dot]:h-1.5 [&_.v.bad]:text-danger [&_>_div]:min-w-0">{children}</div>;
}

export function Stat({ tone, value, label }) {
  return (
    <div>
      <span className="v">{value}</span>
      <span className="l"><i className={`dot w-[7px] h-[7px] rounded-full bg-[var(--faint)] flex-none inline-block [&.ok]:bg-ok [&.warn]:bg-warn [&.bad]:bg-danger [&.info]:bg-info ${TONES[tone] ?? ""}`} />{label}</span>
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
    <button type="button" className={`kpi flex flex-col gap-1 p-[14px_16px] overflow-hidden [&_.spark]:m-[0_-20px] max-compact:p-[14px_14px_0] max-compact:[&_.spark]:m-[0_-14px] text-left font-inherit text-text border-0 bg-transparent! rounded-none cursor-default transition-[border-color_0.15s,_box-shadow_0.15s] [&:hover]:border-line-2 [&.active]:border-brand [&.active]:shadow-none [&_.label]:text-ui-sm [&_.label]:text-muted [&_.label]:font-medium [&_.value]:text-display-sm [&_.value]:font-semibold [&_.value]:tracking-[-0.02em] [&_.value]:leading-[1.25] [&_.sub]:text-xs [&_.sub]:text-muted [&_.sub.bad]:text-danger [&_svg]:w-full [&_svg]:h-[34px] [&_svg]:mt-1.5 [&_svg]:block relative min-h-0 m-0 [border-right:1px_solid_var(--line)] shadow-none! [&:last-child]:border-r-0 [button&]:cursor-pointer [button&:hover]:bg-hover! [&.active]:bg-hover! [&_.value]:text-text [&_.value]:tabular-nums ${active ? "active" : ""}`} onClick={onClick} aria-pressed={active}>
      <span className="label whitespace-nowrap">{label}</span>
      <span className="value">{value}</span>
      <span className={`sub ${bad ? "bad" : ""}`}>{sub}</span>
      <Spark values={values} color={color} />
    </button>
  );
}
