import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "@inertiajs/react";
import Hair from "./Hair";
import { CATEGORIES, SERVICES, readPins, readRecent, writePins } from "../lib/services";

/** The Services menu: a search, the categories, and every service with its description. Starred ones sit in the top bar. */
export default function ServicesMenu({ hrefFor, current, pins, onPins }) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("all");
  const [query, setQuery] = useState("");
  const ref = useRef(null);
  const field = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    setTimeout(() => field.current?.focus(), 0);
    const onKey = (event) => event.key === "Escape" && setOpen(false);
    const onDown = (event) => ref.current && !ref.current.contains(event.target) && setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("mousedown", onDown); };
  }, [open]);
  const pin = (event, key) => {
    event.preventDefault();
    event.stopPropagation();
    const next = pins.includes(key) ? pins.filter((item) => item !== key) : [...pins, key];
    writePins(next);
    onPins(next);
  };
  const recent = useMemo(() => readRecent().map((key) => SERVICES.find((service) => service.key === key)).filter(Boolean), [open]);
  const needle = query.trim().toLowerCase();
  const shown = SERVICES.filter((service) => (category === "all" || service.category === category)
    && (!needle || `${service.label} ${service.description} ${service.pages.map(([, label]) => label).join(" ")}`.toLowerCase().includes(needle)));
  return (
    <div className="services-wrap" ref={ref}>
      <button type="button" className={`services-btn ${open ? "open" : ""}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Hair name="services" size={17} /> Services <Hair name="chevron" size={13} className="faint" />
      </button>
      {open && (
        <div className="services-panel" role="dialog" aria-label="Services">
          <div className="services-search">
            <Hair name="search" size={17} />
            <input ref={field} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find a service or a page" spellCheck={false} />
            <kbd>esc</kbd>
          </div>
          <div className="services-body">
            <aside className="services-cats">
              <button type="button" className={category === "all" ? "active" : ""} onClick={() => setCategory("all")}>All services</button>
              {CATEGORIES.map((item) => (
                <button key={item.id} type="button" className={category === item.id ? "active" : ""} onClick={() => setCategory(item.id)}>{item.label}</button>
              ))}
            </aside>
            <div className="services-main">
              {recent.length > 0 && category === "all" && !needle && (
                <div className="services-recent">
                  <span className="eyebrow">Recent</span>
                  <div className="row wrap">
                    {recent.map((service) => (
                      <Link key={service.key} href={hrefFor(service.pages[0][0])} className="chip" onClick={() => setOpen(false)}><Hair name={service.icon} size={15} />{service.label}</Link>
                    ))}
                  </div>
                </div>
              )}
              <div className="services-grid">
                {shown.map((service) => (
                  <Link key={service.key} href={hrefFor(service.pages[0][0])} className={`service-row ${current === service.key ? "current" : ""}`} onClick={() => setOpen(false)}>
                    <Hair name={service.icon} size={22} />
                    <span className="grow">
                      <b>{service.label}</b>
                      <small>{service.description}</small>
                    </span>
                    <button type="button" className={`pin ${pins.includes(service.key) ? "on" : ""}`} aria-label={pins.includes(service.key) ? "Unpin" : "Pin to the top bar"} title={pins.includes(service.key) ? "Unpin from the top bar" : "Pin to the top bar"} onClick={(event) => pin(event, service.key)}><Hair name="star" size={16} /></button>
                  </Link>
                ))}
                {shown.length === 0 && <p className="muted">Nothing matches “{query}”.</p>}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function usePins() {
  const [pins, setPins] = useState(() => readPins());
  return [pins, setPins];
}
