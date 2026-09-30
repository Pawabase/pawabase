import { useEffect, useRef, useState } from "react";

// Small dependency-free time-series charts drawn in real pixels (so text and
// strokes never stretch), with a hover crosshair and tooltip.
//
// series: [{ key, label, color, type: "area" | "line" | "bar" }]
// data:   [{ t: iso, [key]: number }]

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function niceMax(value) {
  if (value <= 0) return 4;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) if (step * power >= value) return step * power;
  return 10 * power;
}

export function compact(n) {
  if (n == null) return "–";
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (Math.abs(n) >= 1e4) return `${Math.round(n / 1e3)}k`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, "")}k`;
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function timeLabel(iso, stepSeconds, long) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const day = d.toLocaleDateString([], { month: "short", day: "numeric" });
  if (stepSeconds < 3600) return time;
  if (stepSeconds < 86400) return long || stepSeconds > 3600 ? `${day} ${time}` : time;
  return day;
}

function smoothPath(points) {
  if (points.length < 2) return points.length ? `M${points[0][0]},${points[0][1]}` : "";
  let d = `M${points[0][0]},${points[0][1]}`;
  for (let i = 1; i < points.length; i += 1) {
    const [x0, y0] = points[i - 1];
    const [x1, y1] = points[i];
    const mx = (x0 + x1) / 2;
    d += ` C${mx},${y0} ${mx},${y1} ${x1},${y1}`;
  }
  return d;
}

export default function Chart({ series, data, height = 220, stepSeconds, format = compact, empty = "No activity in this range yet." }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const pad = { top: 12, right: 8, bottom: 26, left: 40 };
  const innerW = Math.max(0, width - pad.left - pad.right);
  const innerH = height - pad.top - pad.bottom;
  const stacked = series.filter((s) => s.type === "bar");
  const peak = Math.max(
    0,
    ...data.map((row) => Math.max(
      stacked.reduce((sum, s) => sum + (row[s.key] || 0), 0),
      ...series.filter((s) => s.type !== "bar").map((s) => row[s.key] || 0),
    )),
  );
  const top = niceMax(peak);
  const n = data.length;
  const slot = n ? innerW / n : 0;
  const x = (i) => pad.left + slot * i + slot / 2;
  const y = (v) => pad.top + innerH - (v / top) * innerH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => top * f);
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(innerW / 90))));
  const allZero = peak === 0;

  function onMove(e) {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - box.left - pad.left) / slot);
    setHover(i >= 0 && i < n ? i : null);
  }

  return (
    <div className="chart" ref={ref} style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} className="chart-grid" />
              <text x={pad.left - 8} y={y(t) + 4} textAnchor="end" className="chart-axis">{format(t)}</text>
            </g>
          ))}
          {data.map((row, i) => (i % every === 0 ? (
            <text key={row.t} x={x(i)} y={height - 6} textAnchor="middle" className="chart-axis">{timeLabel(row.t, stepSeconds)}</text>
          ) : null))}
          {hover != null && <rect x={pad.left + slot * hover} y={pad.top} width={slot} height={innerH} className="chart-hover" />}
          {stacked.length > 0 && data.map((row, i) => {
            let base = 0;
            const w = Math.max(2, Math.min(22, slot * 0.62));
            return (
              <g key={row.t}>
                {stacked.map((s, k) => {
                  const v = row[s.key] || 0;
                  if (!v) return null;
                  const y1 = y(base + v);
                  const h = y(base) - y1;
                  base += v;
                  const last = k === stacked.length - 1 || stacked.slice(k + 1).every((o) => !row[o.key]);
                  return <rect key={s.key} x={x(i) - w / 2} y={y1} width={w} height={Math.max(1, h)} rx={last ? Math.min(5, w / 2) : 0} style={{ fill: s.color }} />;
                })}
              </g>
            );
          })}
          {series.filter((s) => s.type !== "bar").map((s) => {
            // A null value (no data, e.g. latency with no requests) breaks the line.
            const runs = [];
            data.forEach((row, i) => {
              if (row[s.key] == null) runs.push(null);
              else {
                if (!runs.length || runs[runs.length - 1] === null) runs.push([]);
                runs[runs.length - 1].push([x(i), y(row[s.key])]);
              }
            });
            const segments = runs.filter(Boolean);
            const line = segments.map(smoothPath).join(" ");
            const area = segments
              .filter((pts) => pts.length > 1)
              .map((pts) => `${smoothPath(pts)} L${pts[pts.length - 1][0]},${y(0)} L${pts[0][0]},${y(0)} Z`)
              .join(" ");
            const gid = `g-${s.key}`;
            return (
              <g key={s.key}>
                {s.type === "area" && (
                  <>
                    <defs>
                      <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" style={{ stopColor: s.color, stopOpacity: 0.28 }} />
                        <stop offset="100%" style={{ stopColor: s.color, stopOpacity: 0 }} />
                      </linearGradient>
                    </defs>
                    {area && <path d={area} fill={`url(#${gid})`} />}
                  </>
                )}
                <path d={line} fill="none" style={{ stroke: s.color }} strokeWidth={s.type === "area" ? 2.25 : 2} strokeLinecap="round" strokeDasharray={s.dashed ? "4 4" : undefined} />
                {segments.map((pts) => pts.length === 1 && <circle key={pts[0][0]} cx={pts[0][0]} cy={pts[0][1]} r={3.5} style={{ fill: s.color }} />)}
                {hover != null && data[hover][s.key] != null && <circle cx={x(hover)} cy={y(data[hover][s.key])} r={4} className="chart-dot" style={{ stroke: s.color }} />}
              </g>
            );
          })}
        </svg>
      )}
      {allZero && width > 0 && <div className="chart-empty">{empty}</div>}
      {hover != null && data[hover] && (
        <div className="chart-tip" style={{ left: Math.min(Math.max(x(hover), 90), width - 90), top: 4 }}>
          <b>{timeLabel(data[hover].t, stepSeconds, true)}</b>
          {series.map((s) => (
            <div key={s.key} className="row" style={{ gap: 8 }}>
              <span className="swatch" style={{ background: s.color }} />
              <span className="grow">{s.label}</span>
              <b>{data[hover][s.key] == null ? "–" : format(data[hover][s.key])}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Sparkline({ values, color, height = 36 }) {
  const [ref, width] = useWidth();
  values = values.map((v) => v ?? 0);
  const peak = Math.max(1, ...values);
  const points = values.map((v, i) => [
    values.length > 1 ? (i / (values.length - 1)) * width : width / 2,
    height - 3 - (v / peak) * (height - 6),
  ]);
  const line = smoothPath(points);
  return (
    <div ref={ref} style={{ height }} className="spark">
      {width > 0 && values.length > 1 && (
        <svg width={width} height={height}>
          <path d={`${line} L${width},${height} L0,${height} Z`} style={{ fill: color, opacity: 0.14 }} />
          <path d={line} fill="none" style={{ stroke: color }} strokeWidth={2} strokeLinecap="round" />
        </svg>
      )}
    </div>
  );
}
