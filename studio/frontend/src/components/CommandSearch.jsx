import { router, usePage } from "@inertiajs/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./icons";
import { ENV_NAV, envHref } from "./Layout";

// Jump to any page: this environment's sections, the other environments, and
// the runtime pages. ⌘K or / focuses it.

function score(text, query) {
  if (!query) return 1;
  const hay = text.toLowerCase();
  const q = query.toLowerCase().trim();
  if (hay.startsWith(q)) return 100;
  const at = hay.indexOf(q);
  if (at >= 0) return 80 - at;
  // Letters in order ("mt" → "mail templates").
  let i = 0;
  for (const ch of hay) if (ch === q[i]) i += 1;
  return i === q.length ? 20 : 0;
}

export default function CommandSearch() {
  const { props } = usePage();
  const { runtime, envs, env } = props;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef(null);
  const box = useRef(null);

  useEffect(() => {
    function onKey(e) {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
      } else if (e.key === "/" && !typing) {
        e.preventDefault();
        input.current?.focus();
      }
    }
    function onClick(e) {
      if (box.current && !box.current.contains(e.target)) setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, []);

  const load = () => setOpen(true);

  const items = useMemo(() => {
    const all = [];
    if (env) {
      for (const group of ENV_NAV) {
        for (const [key, label] of group.items) {
          all.push({ group: `${runtime?.name || "Pawabase"} · ${env}`, label, hint: group.title, icon: key, href: envHref(env, key) });
        }
      }
    }
    for (const e of envs || []) {
      if (e.name !== env) all.push({ group: "Environments", label: `Switch to ${e.name}`, hint: runtime?.name, icon: "layers", href: envHref(e.name, "overview") });
    }
    all.push({ group: "Runtime", label: "Audit log", hint: "What changed", icon: "audit", href: "/audit" });
    return all
      .map((item) => ({ ...item, rank: Math.max(score(item.label, query), score(item.hint || "", query) * 0.6) }))
      .filter((item) => item.rank > 0)
      .sort((a, b) => (query ? b.rank - a.rank : 0))
      .slice(0, 14);
  }, [runtime, env, envs, query]);

  useEffect(() => setActive(0), [query]);

  function go(item) {
    setOpen(false);
    setQuery("");
    input.current?.blur();
    router.visit(item.href);
  }

  function onKeyDown(e) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(items.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter" && items[active]) {
      e.preventDefault();
      go(items[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
      input.current?.blur();
    }
  }

  let lastGroup = null;
  return (
    <div className={`cmd relative [&.open_.cmd-field]:w-[320px] [&.open_.cmd-field]:shadow-none [&.open_.cmd-field]:text-text max-navigation:[&.open_.cmd-field]:w-[min(240px,_52vw)] max-navigation:[&.open_.cmd-field]:p-[0_12px] max-navigation:[&.open_.cmd-field]:justify-start max-navigation:[&:not(.open)_.cmd-field_input]:absolute max-navigation:[&:not(.open)_.cmd-field_input]:w-px max-navigation:[&:not(.open)_.cmd-field_input]:opacity-0 ${open ? "open" : ""}`} ref={box}>
      <label className="cmd-field flex items-center gap-2 h-10 w-[240px] p-[0_8px_0_14px] rounded-sm bg-surface text-muted cursor-text transition-[width_0.2s_ease,_box-shadow_0.15s] [&:focus-within]:w-[320px] [&:focus-within]:shadow-none [&:focus-within]:text-text [&_input]:flex-1 [&_input]:min-w-0 [&_input]:border-0 [&_input]:bg-none [&_input]:p-0 [&_input]:h-full [&_input]:font-inherit [&_input]:text-body [&_input]:text-text [&_input]:shadow-none [&_input:focus]:shadow-none [&_kbd]:[font:600_11px_var(--sans)] [&_kbd]:text-faint [&_kbd]:bg-transparent [&_kbd]:rounded-xs [&_kbd]:p-[1px_5px] max-navigation:w-10 max-navigation:p-0 max-navigation:justify-center max-navigation:[&:focus-within]:w-[min(240px,_52vw)] max-navigation:[&:focus-within]:p-[0_12px] max-navigation:[&:focus-within]:justify-start max-navigation:[&_kbd]:hidden [&_kbd]:[border:1px_solid_var(--line-2)]">
        <Icon name="search" size={16} />
        <input
          ref={input}
          value={query}
          placeholder="Jump to…"
          aria-label="Search pages"
          onFocus={load}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onKeyDown={onKeyDown}
          role="combobox"
          aria-expanded={open}
          aria-controls="cmd-results"
        />
        <kbd>⌘K</kbd>
      </label>
      {open && (
        <div className="cmd-results absolute right-0 top-[calc(100%_+_6px)] w-[380px] max-h-[min(460px,_70vh)] overflow-auto bg-surface rounded-sm shadow-none p-1 z-[30] max-navigation:w-[min(340px,_calc(100vw_-_32px))] max-navigation:right-[-48px] [border:1px_solid_var(--line-2)] left-[auto]" id="cmd-results" role="listbox">
          {items.length === 0 && <div className="cmd-empty p-[18px_12px] text-muted text-ui">No page matches “{query}”.</div>}
          {items.map((item, i) => {
            const head = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return (
              <div key={item.href + item.label}>
                {head && <div className="cmd-group text-2xs font-bold uppercase tracking-[0.06em] text-faint p-[10px_10px_4px]">{head}</div>}
                <button
                  type="button"
                  role="option"
                  aria-selected={i === active}
                  className={`cmd-item flex items-center gap-2.5 w-full border-0 bg-none font-inherit text-body text-text text-left p-[8px_10px] rounded-xs cursor-pointer [&.active]:bg-press [&.active_.cmd-icon]:bg-surface [&.active_.cmd-icon]:text-brand-strong ${i === active ? "active" : ""}`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => go(item)}
                >
                  <span className="cmd-icon w-[28px] h-[28px] rounded-xs bg-transparent grid place-items-center flex-none text-muted [border:1px_solid_var(--line-2)]"><Icon name={item.icon} size={16} /></span>
                  <span className="grow flex-1 min-w-0">{item.label}</span>
                  <span className="cmd-hint text-xs text-faint whitespace-nowrap">{item.hint}</span>
                  {i === active && <Icon name="enter" size={14} className="faint text-faint" />}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
