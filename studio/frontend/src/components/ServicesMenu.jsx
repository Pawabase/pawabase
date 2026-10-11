import { useEffect, useRef, useState } from "react";
import { Link } from "@inertiajs/react";
import { Icon } from "./icons";
import { CATEGORIES, SERVICES, readPins, readRecent, writePins } from "../lib/services";

/** The "Services" menu: every service by category, with the ones you use most pinned to the top bar. */
export default function ServicesMenu({ hrefFor, current, pins, onPins }) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("all");
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
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
  const recent = readRecent().map((key) => SERVICES.find((service) => service.key === key)).filter(Boolean);
  const shown = SERVICES.filter((service) => category === "all" || service.category === category);
  return (
    <div className="services-wrap" ref={ref}>
      <button type="button" className={`services-btn ${open ? "open" : ""}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="layers" size={16} /> Services <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <div className="services-panel" role="dialog" aria-label="Services">
          <aside className="services-cats">
            <button type="button" className={category === "all" ? "active" : ""} onClick={() => setCategory("all")}>All services</button>
            {CATEGORIES.map((item) => (
              <button key={item.id} type="button" className={category === item.id ? "active" : ""} onClick={() => setCategory(item.id)}>{item.label}</button>
            ))}
          </aside>
          <div className="services-main">
            {recent.length > 0 && category === "all" && (
              <div className="services-recent">
                <span className="eyebrow">Recently visited</span>
                <div className="row wrap">
                  {recent.map((service) => (
                    <Link key={service.key} href={hrefFor(service.pages[0][0])} className="chip" onClick={() => setOpen(false)}><Icon name={service.icon} size={14} />{service.label}</Link>
                  ))}
                </div>
              </div>
            )}
            <div className="services-grid">
              {shown.map((service) => (
                <Link key={service.key} href={hrefFor(service.pages[0][0])} className={`service-row ${current === service.key ? "current" : ""}`} onClick={() => setOpen(false)}>
                  <span className="service-icon"><Icon name={service.icon} size={18} /></span>
                  <span className="grow">
                    <b>{service.label}</b>
                    <small>{service.description}</small>
                  </span>
                  <button type="button" className={`pin ${pins.includes(service.key) ? "on" : ""}`} aria-label={pins.includes(service.key) ? "Unpin" : "Pin to the top bar"} title={pins.includes(service.key) ? "Unpin" : "Pin to the top bar"} onClick={(event) => pin(event, service.key)}>{pins.includes(service.key) ? "★" : "☆"}</button>
                </Link>
              ))}
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
