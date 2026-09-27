import { Head, Link, router, usePage } from "@inertiajs/react";
import { ToastProvider } from "./ui";

export const ENV_NAV = [
  { title: "Build", items: [
    ["overview", "Overview"], ["database", "Database"], ["resources", "Resources"], ["schemas", "Schemas"],
    ["transformers", "Transformers"], ["policies", "Policies"], ["routes", "Routes"], ["functions", "Functions"],
  ] },
  { title: "Automate", items: [
    ["flows", "Flows"], ["subscriptions", "Event subscriptions"], ["schedules", "Schedules"],
    ["webhooks", "Webhooks"], ["inbound-hooks", "Inbound hooks"], ["mail-templates", "Mail templates"],
  ] },
  { title: "Services", items: [["users", "Users & auth"], ["storage", "Storage"], ["realtime", "Realtime"]] },
  { title: "Operate", items: [
    ["jobs", "Jobs & queues"], ["events", "Events & runs"], ["observability", "Observability"],
    ["keys", "API keys"], ["secrets", "Secrets"], ["settings", "Settings"],
  ] },
];

export function envHref(ref, env, section) {
  return section === "overview" ? `/projects/${ref}/${env}` : `/projects/${ref}/${env}/${section}`;
}

export default function Layout({ title, crumbs = [], children, full }) {
  const { props } = usePage();
  const { operator, project, envs, env, section } = props;
  return (
    <ToastProvider>
      <Head title={title} />
      <div className="shell">
        <aside className="sidebar">
          <Link href="/" className="brand">
            <span className="brand-mark">P</span> Pawabase
          </Link>
          <nav className="nav">
            {project && env ? (
              <>
                <div style={{ padding: "4px 6px 8px" }}>
                  <select
                    value={env}
                    onChange={(e) => router.visit(envHref(project.ref, e.target.value, section || "overview"))}
                    aria-label="Environment"
                  >
                    {(envs || []).map((e) => <option key={e.name} value={e.name}>{project.name} · {e.name}</option>)}
                  </select>
                </div>
                {ENV_NAV.map((group) => (
                  <div key={group.title}>
                    <div className="nav-title">{group.title}</div>
                    {group.items.map(([key, label]) => (
                      <Link key={key} href={envHref(project.ref, env, key)} className={section === key ? "active" : ""}>{label}</Link>
                    ))}
                  </div>
                ))}
              </>
            ) : (
              <>
                <div className="nav-title">Platform</div>
                <Link href="/" className={!project && title === "Projects" ? "active" : ""}>Projects</Link>
                <Link href="/audit" className={title === "Audit log" ? "active" : ""}>Audit log</Link>
                {project && (
                  <>
                    <div className="nav-title">{project.name}</div>
                    {(envs || []).map((e) => <Link key={e.name} href={envHref(project.ref, e.name, "overview")}>{e.name}</Link>)}
                  </>
                )}
              </>
            )}
          </nav>
          <div className="sidebar-foot spread">
            <span className="muted" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{operator?.email}</span>
            <Link href="/logout" method="post" as="button" className="btn ghost sm">Sign out</Link>
          </div>
        </aside>
        <main className="main">
          <div className="topbar">
            <div className="crumbs">
              <Link href="/">Projects</Link>
              {project && <><span>/</span><Link href={`/projects/${project.ref}`}>{project.name}</Link></>}
              {env && <><span>/</span><Link href={envHref(project.ref, env, "overview")}>{env}</Link></>}
              {crumbs.map((c, i) => <span key={i} className="row" style={{ gap: 6 }}><span>/</span>{c}</span>)}
            </div>
            {project && env && (
              <a className="btn ghost sm" title="Public when the environment enables public docs (Settings)" href={`${props.gateway_url}/docs/v1/${project.ref}/${env}`} target="_blank" rel="noreferrer">API docs ↗</a>
            )}
          </div>
          <div className={`content ${full ? "full" : ""}`}>{children}</div>
        </main>
      </div>
    </ToastProvider>
  );
}
