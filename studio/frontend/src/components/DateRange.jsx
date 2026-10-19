import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./icons";
import { Button } from "./ui";

const DAY = 86400000;
const MAX_DAYS = 90;
const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());
const addDays = (date, n) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + n);
const sameDay = (a, b) => a && b && a.getTime() === b.getTime();
const iso = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const parse = (text) => { const [y, m, d] = text.split("-").map(Number); return y && m && d ? new Date(y, m - 1, d) : null; };
export const formatDay = (date) => date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });

const PRESETS = [
  ["Today", () => { const t = startOfDay(new Date()); return [t, t]; }],
  ["Yesterday", () => { const t = addDays(startOfDay(new Date()), -1); return [t, t]; }],
  ["Last 7 days", () => { const t = startOfDay(new Date()); return [addDays(t, -6), t]; }],
  ["Last 14 days", () => { const t = startOfDay(new Date()); return [addDays(t, -13), t]; }],
  ["Last 30 days", () => { const t = startOfDay(new Date()); return [addDays(t, -29), t]; }],
  ["Last 90 days", () => { const t = startOfDay(new Date()); return [addDays(t, -(MAX_DAYS - 1)), t]; }],
];

/** The ISO timestamps an analytics request sends for a picked pair of days: midnight of the first to midnight after the last. */
export function rangeParams([from, to]) {
  return { from: from.toISOString(), to: addDays(to, 1).toISOString() };
}

function Month({ month, from, to, hover, today, onPick, onHover }) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const lead = (first.getDay() + 6) % 7;
  const total = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const cells = [...Array(lead).fill(null), ...Array.from({ length: total }, (_, i) => new Date(month.getFullYear(), month.getMonth(), i + 1))];
  const end = to || hover;
  const lo = from && end ? (from <= end ? from : end) : from;
  const hi = from && end ? (from <= end ? end : from) : from;
  return (
    <div className="cal-grid grid grid-cols-[repeat(7,_1fr)] gap-[2px_0]" role="grid">
      {WEEKDAYS.map((d) => <span key={d} className="cal-dow text-center text-2xs font-bold text-faint p-[4px_0_6px]">{d}</span>)}
      {cells.map((day, i) => {
        if (!day) return <span key={`b${i}`} />;
        const future = day > today;
        const tooOld = day < addDays(today, -(MAX_DAYS - 1));
        const inside = lo && hi && day >= lo && day <= hi;
        const edge = sameDay(day, lo) || sameDay(day, hi);
        return (
          <button
            key={day.getTime()}
            type="button"
            disabled={future || tooOld}
            className={`cal-day h-[34px] border-0 bg-none text-text font-inherit text-ui cursor-pointer rounded-none [&:hover:not(:disabled)]:bg-sunken [&:disabled]:text-line-2 [&:disabled]:cursor-not-allowed [&.today]:font-extrabold [&.today]:text-brand-strong [&.in]:bg-brand-soft [&.edge]:bg-ink [&.edge]:text-on-ink [&.edge]:rounded-sm ${inside ? "in" : ""} ${edge ? "edge" : ""} ${sameDay(day, today) ? "today" : ""}`}
            onClick={() => onPick(day)}
            onMouseEnter={() => onHover(day)}
            aria-label={day.toDateString()}
            aria-pressed={edge}
          >
            {day.getDate()}
          </button>
        );
      })}
    </div>
  );
}

/** A range picker: presets, a month calendar where you click the first and last day, and typed dates. */
export default function DateRange({ value, onApply, active }) {
  const today = useMemo(() => startOfDay(new Date()), []);
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState(value?.[0] || null);
  const [to, setTo] = useState(value?.[1] || null);
  const [hover, setHover] = useState(null);
  const [month, setMonth] = useState(() => new Date((value?.[1] || today).getFullYear(), (value?.[1] || today).getMonth(), 1));
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const key = (event) => { if (event.key === "Escape") setOpen(false); };
    const down = (event) => { if (ref.current && !ref.current.contains(event.target)) setOpen(false); };
    window.addEventListener("keydown", key);
    window.addEventListener("pointerdown", down);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("pointerdown", down); };
  }, [open]);

  const pick = (day) => {
    if (!from || (from && to)) { setFrom(day); setTo(null); return; }
    if (day < from) { setTo(from); setFrom(day); } else setTo(day);
  };
  const last = to || from;
  const span = from && last ? Math.round((last - from) / DAY) + 1 : 0;
  const valid = from && last && span <= MAX_DAYS;
  const apply = () => { if (valid) { onApply([from, last]); setOpen(false); } };
  const label = value ? (sameDay(value[0], value[1]) ? formatDay(value[0]) : `${formatDay(value[0])} – ${formatDay(value[1])}`) : "Custom";
  const shift = (n) => setMonth((m) => new Date(m.getFullYear(), m.getMonth() + n, 1));
  const atNow = month.getFullYear() === today.getFullYear() && month.getMonth() === today.getMonth();

  return (
    <div className="daterange relative" ref={ref}>
      <button type="button" className={`range-btn inline-flex items-center gap-2 h-8 p-[0_14px] [border:1px_solid_var(--line-2)] rounded-sm bg-surface text-muted font-inherit text-ui-sm font-semibold cursor-pointer [&:hover]:text-text [&:hover]:border-faint [&[aria-expanded=true]]:text-text [&[aria-expanded=true]]:border-faint [&.active]:bg-brand-soft [&.active]:border-transparent [&.active]:text-brand-strong ${active ? "active" : ""}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Icon name="schedules" size={14} />
        <span>{label}</span>
      </button>
      {open && (
        <div className="range-pop absolute right-0 top-[calc(100%_+_8px)] z-[30] flex bg-surface [border:1px_solid_var(--line-2)] rounded-sm max-tablet:flex-col max-tablet:right-[auto] max-tablet:left-0 max-tablet:max-w-[calc(100vw_-_32px)] shadow-none" role="dialog" aria-label="Choose a date range">
          <div className="range-presets grid content-start gap-0.5 p-2.5 min-w-[150px] [border-right:1px_solid_var(--line)] bg-sunken [&_button]:text-left [&_button]:p-[7px_10px] [&_button]:border-0 [&_button]:rounded-sm [&_button]:bg-none [&_button]:text-text [&_button]:font-inherit [&_button]:text-ui [&_button]:cursor-pointer [&_button:hover]:bg-surface max-tablet:grid-cols-[repeat(3,_1fr)] max-tablet:border-r-0 max-tablet:[border-bottom:1px_solid_var(--line)]">
            {PRESETS.map(([name, make]) => (
              <button key={name} type="button" onClick={() => { const [a, b] = make(); setFrom(a); setTo(b); setMonth(new Date(b.getFullYear(), b.getMonth(), 1)); }}>{name}</button>
            ))}
          </div>
          <div className="range-cal p-[14px_16px_12px] w-[292px]" onMouseLeave={() => setHover(null)}>
            <div className="cal-head flex items-center justify-between mb-2 [&_.icon-btn]:w-[30px] [&_.icon-btn]:h-[30px]">
              <button type="button" className="icon-btn w-control h-control rounded-[calc(var(--r)_-_1px)] grid place-items-center border-0 bg-transparent text-text cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong" onClick={() => shift(-1)} aria-label="Previous month"><Icon name="chevronRight" size={16} className="rotate-180 transform-[rotate(180deg)]" /></button>
              <b>{month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</b>
              <button type="button" className="icon-btn w-control h-control rounded-[calc(var(--r)_-_1px)] grid place-items-center border-0 bg-transparent text-text cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong" onClick={() => shift(1)} disabled={atNow} aria-label="Next month"><Icon name="chevronRight" size={16} /></button>
            </div>
            <Month month={month} from={from} to={to} hover={hover} today={today} onPick={pick} onHover={setHover} />
            <div className="range-fields grid grid-cols-2 gap-2.5 mt-3 [&_label]:grid [&_label]:gap-1 [&_label]:text-caption [&_label]:font-semibold [&_label]:text-muted [&_input]:p-[6px_8px] [&_input]:text-ui-sm [&_input]:rounded-sm">
              <label>From<input type="date" value={from ? iso(from) : ""} max={iso(today)} onChange={(e) => { const d = parse(e.target.value); if (d) { setFrom(d); if (to && d > to) setTo(null); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); } }} /></label>
              <label>To<input type="date" value={last ? iso(last) : ""} max={iso(today)} onChange={(e) => { const d = parse(e.target.value); if (d) setTo(d); }} /></label>
            </div>
            <div className="range-foot flex items-center justify-between gap-2.5 mt-3 pt-2.5 [border-top:1px_solid_var(--line)]">
              <span className={`small text-ui-sm ${span > MAX_DAYS ? "danger-text" : "muted"}`}>{span ? `${span} day${span === 1 ? "" : "s"}${span > MAX_DAYS ? ` (most is ${MAX_DAYS})` : ""}` : "Pick a first and last day"}</span>
              <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                <Button size="sm" onClick={() => setOpen(false)}>Cancel</Button>
                <Button size="sm" variant="primary" disabled={!valid} onClick={apply}>Apply</Button>
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
