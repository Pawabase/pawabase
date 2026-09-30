import { router, usePage } from "@inertiajs/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { get } from "../lib/api";
import { Icon } from "./icons";
import { ENV_NAV, envHref } from "./Layout";

// Jump to any page: this environment's sections, the project's other
// environments, every project, and the platform pages. ⌘K or / focuses it.

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
  const { project, envs, env } = props;
  const orgs = props.orgs || [];
  const orgSlug = props.org?.slug || project?.org;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [projects, setProjects] = useState(null);
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

  function load() {
    setOpen(true);
    if (projects === null) {
      setProjects([]);
      get("/projects").then((r) => setProjects(r.data || r || [])).catch(() => setProjects([]));
    }
  }

  const items = useMemo(() => {
    const all = [];
    if (project && env) {
      for (const group of ENV_NAV) {
        for (const [key, label] of group.items) {
          all.push({ group: `${project.name} · ${env}`, label, hint: group.title, icon: key, href: envHref(project.ref, env, key) });
        }
      }
      for (const e of envs || []) {
        if (e.name !== env) all.push({ group: "Environments", label: `Switch to ${e.name}`, hint: project.name, icon: "layers", href: envHref(project.ref, e.name, "overview") });
      }
    }
    for (const p of projects || []) {
      all.push({ group: "Projects", label: p.name, hint: p.ref, icon: "projects", href: `/projects/${p.ref}` });
    }
    for (const o of orgs) {
      all.push({ group: "Organizations", label: o.name, hint: `${o.slug} · switch`, icon: "org", href: `/orgs/${o.slug}` });
    }
    if (orgSlug) {
      all.push({ group: "Organization", label: "Team", hint: "Members, roles, invitations", icon: "team", href: `/orgs/${orgSlug}/team` });
      all.push({ group: "Organization", label: "Audit log", hint: "Who changed what", icon: "audit", href: `/orgs/${orgSlug}/audit` });
      all.push({ group: "Organization", label: "Organization settings", hint: "Name, danger zone", icon: "settings", href: `/orgs/${orgSlug}/settings` });
    }
    all.push({ group: "Organizations", label: "New organization", hint: "Create", icon: "plus", href: "/orgs/new" });
    return all
      .map((item) => ({ ...item, rank: Math.max(score(item.label, query), score(item.hint || "", query) * 0.6) }))
      .filter((item) => item.rank > 0)
      .sort((a, b) => (query ? b.rank - a.rank : 0))
      .slice(0, 14);
  }, [project, env, envs, projects, orgs, orgSlug, query]);

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
    <div className={`cmd ${open ? "open" : ""}`} ref={box}>
      <label className="cmd-field">
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
        <div className="cmd-results" id="cmd-results" role="listbox">
          {items.length === 0 && <div className="cmd-empty">No page matches “{query}”.</div>}
          {items.map((item, i) => {
            const head = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return (
              <div key={item.href + item.label}>
                {head && <div className="cmd-group">{head}</div>}
                <button
                  type="button"
                  role="option"
                  aria-selected={i === active}
                  className={`cmd-item ${i === active ? "active" : ""}`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => go(item)}
                >
                  <span className="cmd-icon"><Icon name={item.icon} size={16} /></span>
                  <span className="grow">{item.label}</span>
                  <span className="cmd-hint">{item.hint}</span>
                  {i === active && <Icon name="enter" size={14} className="faint" />}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
