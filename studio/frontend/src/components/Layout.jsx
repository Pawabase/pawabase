import { Head, Link, router, usePage } from "@inertiajs/react";
import { useEffect, useState } from "react";
import CommandSearch from "./CommandSearch";
import Console from "./Console";
import StatusLights from "./StatusLights";
import { Icon } from "./icons";
import { Logo } from "./Logo";
import { ToastProvider } from "./ui";

export const ENV_NAV = [
  { title: "Build", items: [
    ["overview", "Overview"], ["database", "Database"], ["resources", "Resources"], ["schemas", "Schemas"],
    ["transformers", "Transformers"], ["policies", "Policies"], ["routes", "Routes"], ["explorer", "API Explorer"], ["functions", "Functions"],
  ] },
  { title: "Automate", items: [
    ["flows", "Flows"], ["subscriptions", "Event subscriptions"], ["schedules", "Schedules"],
    ["webhooks", "Webhooks"], ["inbound-hooks", "Inbound hooks"], ["mail-templates", "Mail templates"],
  ] },
  { title: "Services", items: [["users", "Users & auth"], ["storage", "Storage"], ["realtime", "Realtime"]] },
  { title: "Operate", items: [
    ["releases", "Releases & versions"],
    ["jobs", "Jobs & queues"], ["events", "Events & runs"], ["observability", "Observability"],
    ["keys", "API keys"], ["secrets", "Secrets"], ["settings", "Settings"],
  ] },
];

// The pastel each section wears on its sheet headers and empty states.
export const SECTION_TONES = {
  resources: "lavender", schemas: "sky", transformers: "butter", policies: "mint", routes: "peach",
  functions: "sky", flows: "lavender", subscriptions: "rose", schedules: "butter", webhooks: "peach",
  "inbound-hooks": "mint", "mail-templates": "rose", buckets: "sky", storage: "sky",
};

const ENV_TONES = ["lavender", "peach", "mint", "butter", "sky", "rose"];

export function envTone(name, index = 0) {
  if (name === "production") return "peach";
  if (name === "development") return "mint";
  if (name === "staging") return "butter";
  return ENV_TONES[index % ENV_TONES.length];
}

export const ORG_ROLE_LABELS = { owner: "Owner", admin: "Admin", developer: "Developer", viewer: "Viewer" };

/** Whether *role* is at least *need* (viewer < developer < admin < owner). */
export function atLeast(role, need) {
  const order = ["viewer", "developer", "admin", "owner"];
  return order.indexOf(role) >= order.indexOf(need);
}

export function envHref(ref, env, section) {
  return section === "overview" ? `/projects/${ref}/${env}` : `/projects/${ref}/${env}/${section}`;
}

function readTheme() {
  try {
    return localStorage.getItem("pawabase.theme") || "system";
  } catch {
    return "system";
  }
}

function useTheme() {
  const [theme, setTheme] = useState(readTheme);
  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("pawabase.theme", theme);
    } catch {
      /* private mode: the choice lasts for this page only */
    }
  }, [theme]);
  const dark = theme === "dark" || (theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  return [dark, () => setTheme(dark ? "light" : "dark")];
}

export default function Layout({ title, crumbs = [], children, full }) {
  const { props, url } = usePage();
  const { operator, project, envs, env, section, orgs = [] } = props;
  const orgSlug = props.org?.slug || project?.org;
  const org = orgs.find((o) => o.slug === orgSlug) || null;
  const orgRole = org?.role;
  const [dark, toggleTheme] = useTheme();
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [url]);
  const envIndex = Math.max(0, (envs || []).findIndex((e) => e.name === env));
  return (
    <ToastProvider>
      <Head title={title} />
      <div className={`shell ${navOpen ? "nav-open" : ""}`}>
        <aside className="sidebar">
          <Link href="/" className="brand"><Logo sub="Studio" /></Link>
          {project && env && (
            <label className="switcher" title="Switch environment">
              <span className={`avatar pastel ${envTone(env, envIndex)}`}>{project.name.slice(0, 1).toUpperCase()}</span>
              <span className="grow">
                <b>{project.name}</b>
                <span className="sub">{env}</span>
              </span>
              <Icon name="chevronDown" size={16} className="faint" />
              <select
                value={env}
                onChange={(e) => router.visit(envHref(project.ref, e.target.value, section || "overview"))}
                aria-label="Environment"
              >
                {(envs || []).map((e) => <option key={e.name} value={e.name}>{project.name} · {e.name}</option>)}
              </select>
            </label>
          )}
          <nav className="nav">
            {project && env ? (
              ENV_NAV.map((group) => (
                <div key={group.title}>
                  <div className="nav-title">{group.title}</div>
                  {group.items.map(([key, label]) => (
                    <Link key={key} href={envHref(project.ref, env, key)} className={section === key ? "active" : ""}>
                      <Icon name={key} />{label}
                    </Link>
                  ))}
                </div>
              ))
            ) : (
              <>
                <div className="nav-title">{org ? org.name : "Studio"}</div>
                {org && (
                  <>
                    <Link href={`/orgs/${org.slug}`} className={!project && title === "Projects" ? "active" : ""}><Icon name="projects" />Projects</Link>
                    <Link href={`/orgs/${org.slug}/team`} className={title === "Team" ? "active" : ""}><Icon name="team" />Team</Link>
                    <Link href={`/orgs/${org.slug}/audit`} className={title === "Audit log" ? "active" : ""}><Icon name="audit" />Audit log</Link>
                    {atLeast(orgRole, "admin") && <Link href={`/orgs/${org.slug}/settings`} className={title === "Organization settings" ? "active" : ""}><Icon name="settings" />Settings</Link>}
                  </>
                )}
                {project && (
                  <>
                    <div className="nav-title">{project.name}</div>
                    {(envs || []).map((e, i) => (
                      <Link key={e.name} href={envHref(project.ref, e.name, "overview")}>
                        <span className={`avatar pastel ${envTone(e.name, i)}`} style={{ width: 18, height: 18, borderRadius: 6, fontSize: 10 }}>{e.name.slice(0, 1).toUpperCase()}</span>
                        {e.name}
                      </Link>
                    ))}
                  </>
                )}
              </>
            )}
          </nav>
          {project && env && (
            <div className="sidebar-card">
              <p>Your API is live. Browse the generated docs.</p>
              <a className="btn sm" href={`/projects/${project.ref}/${env}/api-docs`} target="_blank" rel="noreferrer" title="Always available to operators. Publish them at /docs/v1 with public_docs in Settings.">
                <Icon name="external" /> Open API docs
              </a>
            </div>
          )}
          <div className="sidebar-foot">
            <span className="avatar">{(operator?.email || "?").slice(0, 1).toUpperCase()}</span>
            <span className="grow muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{operator?.email}</span>
            <Link href="/logout" method="post" as="button" className="icon-btn" title="Sign out" style={{ width: 32, height: 32 }}>
              <Icon name="logout" size={16} />
            </Link>
          </div>
        </aside>
        {navOpen && <div className="sheet-overlay" style={{ zIndex: 39 }} onClick={() => setNavOpen(false)} />}
        <main className="main">
          <div className="topbar">
            <div className="row" style={{ minWidth: 0 }}>
              <button type="button" className="icon-btn menu-btn" onClick={() => setNavOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
              {org && (
                <label className="org-pick" title="Switch organization">
                  <span className="avatar pastel lavender"><Icon name="org" size={14} /></span>
                  <b>{org.name}</b>
                  <Icon name="chevronDown" size={15} className="faint" />
                  <select
                    value={org.slug}
                    onChange={(e) => router.visit(e.target.value === "+new" ? "/orgs/new" : `/orgs/${e.target.value}`)}
                    aria-label="Organization"
                  >
                    {orgs.map((o) => <option key={o.slug} value={o.slug}>{o.name}</option>)}
                    <option value="+new">+ New organization…</option>
                  </select>
                </label>
              )}
              <div className="crumbs">
                {!org && <Link href="/">Studio</Link>}
                {project && <><span className="sep">/</span><Link href={`/projects/${project.ref}`}>{project.name}</Link></>}
                {env && <><span className="sep">/</span><Link href={envHref(project.ref, env, "overview")}>{env}</Link></>}
                {crumbs.map((c, i) => <span key={i} className="row" style={{ gap: 4 }}><span className="sep">/</span><span className="here">{c}</span></span>)}
              </div>
            </div>
            <div className="top-actions">
              <StatusLights />
              <CommandSearch />
              <button type="button" className="icon-btn" onClick={toggleTheme} aria-label={dark ? "Switch to light" : "Switch to dark"}>
                <Icon name={dark ? "sun" : "moon"} />
              </button>
            </div>
          </div>
          <div className={`content ${full ? "full" : ""}`}>{children}</div>
        </main>
        <Console project={project} env={env} />
      </div>
    </ToastProvider>
  );
}
