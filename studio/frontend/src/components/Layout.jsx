import { Head, Link, router, usePage } from "@inertiajs/react";
import { useEffect, useState } from "react";
import CommandSearch from "./CommandSearch";
import Console from "./Console";
import StatusLights from "./StatusLights";
import { Icon } from "./icons";
import { Logo } from "./Logo";
import { Badge, Button, Field, Loading, Sheet, Status, Table, ToastProvider, useAction, when } from "./ui";
import { envPath, post, useApi } from "../lib/api";

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
    ["jobs", "Jobs & queues"], ["events", "Events & runs"], ["observability", "Observability"],
    ["keys", "API keys"], ["secrets", "Secrets"], ["backups", "Backups"], ["settings", "Settings"],
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

export function envHref(ref, env, section, child = "") {
  const path = section === "overview" ? `/projects/${ref}/${env}` : `/projects/${ref}/${env}/${section}${child ? `/${child}` : ""}`;
  // Keep branch context in navigation URLs as well as local storage. Server
  // rendered editors then receive the same working tree on their first load.
  try {
    const branch = localStorage.getItem(`pawabase.branch.${ref}.${env}`) || "main";
    return branch === "main" ? path : `${path}?branch=${encodeURIComponent(branch)}`;
  } catch {
    return path;
  }
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

function branchKey(project, env) {
  return `pawabase.branch.${project.ref}.${env}`;
}

function BranchHistory({ project, env, url, onClose }) {
  const branches = useApi(envPath(project.ref, env, "/branches"));
  const revisions = useApi(envPath(project.ref, env, "/revisions"));
  const versions = useApi(envPath(project.ref, env, "/api-versions"));
  const releases = useApi(envPath(project.ref, env, "/releases"));
  const deployments = useApi(envPath(project.ref, env, "/deployments"));
  const [branch, setBranch] = useState(() => {
    try { return localStorage.getItem(branchKey(project, env)) || "main"; } catch { return "main"; }
  });
  const [newBranch, setNewBranch] = useState("");
  const [target, setTarget] = useState("main");
  const [tab, setTab] = useState("branches");
  const [revisionMessage, setRevisionMessage] = useState("");
  const [newVersion, setNewVersion] = useState("");
  const [release, setRelease] = useState({ revision_id: "", api_version: "", name: "", allow_breaking: false });
  const [run, busy] = useAction();
  const branchDraft = useApi(branch === "main" ? null : envPath(project.ref, env, `/branches/${branch}/draft`));
  useEffect(() => {
    const fromUrl = new URLSearchParams(url.split("?")[1] || "").get("branch");
    const next = fromUrl || (() => { try { return localStorage.getItem(branchKey(project, env)) || "main"; } catch { return "main"; } })();
    setBranch(next);
    try { localStorage.setItem(branchKey(project, env), next); } catch { /* session-only fallback */ }
  }, [project.ref, env, url]);
  const choose = (next) => {
    setBranch(next);
    try { localStorage.setItem(branchKey(project, env), next); } catch { /* session-only fallback */ }
    window.dispatchEvent(new CustomEvent("pawabase:branch", { detail: { project: project.ref, env, branch: next } }));
    const target = new URL(window.location.href);
    if (next === "main") target.searchParams.delete("branch");
    else target.searchParams.set("branch", next);
    // Reopen the workspace after Inertia reloads the checked-out definition
    // tree. Only its Done control removes this marker.
    target.searchParams.set("history", "1");
    router.visit(`${target.pathname}${target.search}`, { preserveScroll: true, preserveState: false });
  };
  const items = branches.data?.data || [{ name: "main" }];
  const reload = () => { branches.reload(); revisions.reload(); versions.reload(); releases.reload(); deployments.reload(); branchDraft.reload(); };
  const act = async (fn) => { if (await run(fn)) reload(); };
  const revisionRows = revisions.data?.data || [], versionRows = versions.data?.data || [];
  const validRevisions = revisionRows.filter((item) => item.status === "valid");
  const draft = { ...release, revision_id: release.revision_id || validRevisions[0]?.id || "", api_version: release.api_version || versionRows[0]?.name || "" };
  const createRevision = async () => { if (await run(() => post(envPath(project.ref, env, `/branches/${branch}/revisions`), { message: revisionMessage }), "Revision created")) { setRevisionMessage(""); reload(); } };
  const createVersion = async () => { if (await run(() => post(envPath(project.ref, env, "/api-versions"), { name: newVersion }), "API version created")) { setNewVersion(""); reload(); } };
  const prepareRelease = async () => { if (await run(() => post(envPath(project.ref, env, "/releases"), draft), "Release prepared")) { setRelease({ revision_id: "", api_version: "", name: "", allow_breaking: false }); reload(); } };
  return <Sheet title="History" subtitle={`Working on ${branch} · ${project.name} / ${env}`} icon="gitBranch" tabs={[["branches", "Branches"], ["releases", "Releases"], ["revisions", "Revisions"], ["versions", "API versions"], ["deployments", "Deployments"]].map(([value, label]) => ({ value, label }))} tab={tab} onTab={setTab} onClose={onClose} footer={<Button onClick={onClose}>Done</Button>}>
    {tab === "branches" && <>
    <section className="form-section">
      <div className="form-section-head"><div><h3>Checkout</h3><p>Switch the working definition tree instantly.</p></div></div>
      <div className="row wrap">
        {items.map((item) => <Button key={item.name} size="sm" variant={item.name === branch ? "primary" : ""} disabled={busy || item.name === branch} onClick={() => choose(item.name)}>{item.name}{item.protected ? " · protected" : ""}</Button>)}
      </div>
    </section>
    <section className="form-section">
      <div className="form-section-head"><div><h3>Create branch</h3><p>Starts from the checked-out branch, without changing it.</p></div></div>
      <div className="row"><input value={newBranch} onChange={(event) => setNewBranch(event.target.value)} placeholder="feature-checkout" /><Button variant="primary" disabled={busy || !newBranch} onClick={() => act(async () => { await post(envPath(project.ref, env, "/branches"), { name: newBranch, from_branch: branch === "main" ? null : branch }); const next = newBranch; setNewBranch(""); choose(next); })}>Create & checkout</Button></div>
    </section>
    {branch !== "main" && <section className="form-section">
      <div className="form-section-head"><div><h3>Merge {branch}</h3><p>Applies this branch’s isolated definitions to a target branch.</p></div></div>
      <div className="row"><select value={target} onChange={(event) => setTarget(event.target.value)}>{items.filter((item) => item.name !== branch).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select><Button variant="primary" disabled={busy} onClick={() => act(() => post(envPath(project.ref, env, `/branches/${branch}/merge`), { target }))}>Merge into {target}</Button></div>
      <div className="stack" style={{ gap: 6, marginTop: 12 }}><span className="hint">{branchDraft.data?.branch?.changes?.length || 0} recorded actions</span>{(branchDraft.data?.branch?.changes || []).slice().reverse().slice(0, 8).map((change, index) => <div className="hint" key={`${change.at || index}-${index}`}>{change.action}{change.target ? ` · ${change.target}` : ""}{change.at ? ` · ${when(change.at)}` : ""}</div>)}</div>
    </section>}
    </>}
    {tab === "releases" && <>
      <section className="form-section"><div className="form-section-head"><div><h3>Prepare release</h3><p>Point a stable API version at an immutable revision.</p></div></div><Field label="Revision"><select value={draft.revision_id} onChange={(event) => setRelease({ ...release, revision_id: event.target.value })}>{validRevisions.map((item) => <option key={item.id} value={item.id}>#{item.number} · {item.branch} · {item.message || item.checksum.slice(0, 8)}</option>)}</select></Field><Field label="API version"><select value={draft.api_version} onChange={(event) => setRelease({ ...release, api_version: event.target.value })}>{versionRows.map((item) => <option key={item.id}>{item.name}</option>)}</select></Field><Field label="Release name"><input value={release.name} onChange={(event) => setRelease({ ...release, name: event.target.value })} placeholder="v2.0.0" /></Field><label className="check"><input type="checkbox" checked={release.allow_breaking} onChange={(event) => setRelease({ ...release, allow_breaking: event.target.checked })} /> I acknowledge breaking compatibility findings.</label><div style={{ marginTop: 14 }}><Button variant="primary" disabled={busy || !draft.revision_id || !draft.api_version || !draft.name} onClick={prepareRelease}>Prepare release</Button></div></section>
      <section className="form-section"><div className="form-section-head"><div><h3>Prepared releases</h3></div></div><Loading state={releases} empty="No releases prepared.">{(data) => <Table rows={data.data} columns={[{ label: "Release", render: (item) => <b>{item.name}</b> }, { label: "Path", render: (item) => <code>/rest/{item.api_version}</code> }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Compatibility", render: (item) => item.compatibility?.compatible ? <Badge tone="green">compatible</Badge> : <Badge tone="red">breaking</Badge> }, { label: "Created", render: (item) => when(item.created_at) }, { label: "", render: (item) => <Button size="sm" disabled={busy || item.status === "active"} onClick={() => act(() => post(envPath(project.ref, env, `/releases/${item.id}/activate`), {}))}>Activate</Button> }]} />}</Loading></section>
    </>}
    {tab === "revisions" && <><section className="form-section"><div className="form-section-head"><div><h3>Create revision</h3><p>Capture the current {branch} definition tree.</p></div></div><div className="row"><input value={revisionMessage} onChange={(event) => setRevisionMessage(event.target.value)} placeholder={`Snapshot ${branch}`} /><Button variant="primary" disabled={busy} onClick={createRevision}>Create revision</Button></div></section><Loading state={revisions} empty="No revisions yet.">{(data) => <Table rows={data.data} columns={[{ label: "#", key: "number" }, { label: "Branch", key: "branch" }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Message", key: "message" }, { label: "Created", render: (item) => when(item.created_at) }]} />}</Loading></>}
    {tab === "versions" && <><section className="form-section"><div className="form-section-head"><div><h3>Add API version</h3><p>Creates a stable public path such as v2.</p></div></div><div className="row"><input value={newVersion} onChange={(event) => setNewVersion(event.target.value)} placeholder="v2" /><Button variant="primary" disabled={busy || !/^v[1-9][0-9]*$/.test(newVersion)} onClick={createVersion}>Add version</Button></div></section><Loading state={versions} empty="No API versions yet.">{(data) => <Table rows={data.data} columns={[{ label: "Version", key: "name" }, { label: "Status", render: (item) => <Status value={item.status} /> }]} />}</Loading></>}
    {tab === "deployments" && <Loading state={deployments} empty="No deployments yet.">{(data) => <Table rows={data.data} columns={[{ label: "Version", key: "api_version" }, { label: "Action", key: "action" }, { label: "Release", render: (item) => <code>{item.release_id.slice(0, 8)}</code> }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "When", render: (item) => when(item.created_at) }, { label: "", render: (item) => item.id === data.data.find((row) => row.api_version === item.api_version)?.id && item.previous_release_id ? <Button size="sm" disabled={busy} onClick={() => act(() => post(envPath(project.ref, env, `/api-versions/${item.api_version}/rollback`), {}))}>Rollback</Button> : null }]} />}</Loading>}
  </Sheet>;
}

export default function Layout({ title, crumbs = [], children, full }) {
  const { props, url } = usePage();
  const { operator, project, envs, env, section, orgs = [] } = props;
  const orgSlug = props.org?.slug || project?.org;
  const org = orgs.find((o) => o.slug === orgSlug) || null;
  const orgRole = org?.role;
  const [dark, toggleTheme] = useTheme();
  const [navOpen, setNavOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(() => new URLSearchParams(url.split("?")[1] || "").has("history"));
  useEffect(() => setNavOpen(false), [url]);
  useEffect(() => setHistoryOpen(new URLSearchParams(url.split("?")[1] || "").has("history")), [url]);
  const closeHistory = () => {
    setHistoryOpen(false);
    const target = new URL(window.location.href);
    target.searchParams.delete("history");
    window.history.replaceState({}, "", `${target.pathname}${target.search}`);
  };
  const envIndex = Math.max(0, (envs || []).findIndex((e) => e.name === env));
  return (
    <ToastProvider>
      <Head title={title} />
      <div className={`shell ${navOpen ? "nav-open" : ""}`}>
        <aside className="sidebar">
          <Link href="/" className="brand"><Logo sub="Studio" /></Link>
          {project && env && (
            <>
              <label className="switcher" title="Switch environment">
                <span className={`avatar pastel ${envTone(env, envIndex)}`}>{project.name.slice(0, 1).toUpperCase()}</span>
                <span className="grow">
                  <b>{project.name}</b>
                  <span className="sub">{env}</span>
                </span>
                <Icon name="chevronDown" size={16} className="faint" />
                <select value={env} onChange={(e) => router.visit(envHref(project.ref, e.target.value, section || "overview"))} aria-label="Environment">
                  {(envs || []).map((e) => <option key={e.name} value={e.name}>{project.name} · {e.name}</option>)}
                </select>
              </label>
            </>
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
              {project && env && <Button size="sm" onClick={() => setHistoryOpen(true)} title="Checkout, create, merge, and review branches"><Icon name="gitBranch" size={15} /> History</Button>}
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
        {historyOpen && project && env && <BranchHistory project={project} env={env} url={url} onClose={closeHistory} />}
      </div>
    </ToastProvider>
  );
}
