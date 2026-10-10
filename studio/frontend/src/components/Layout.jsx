import { Head, Link, router, usePage } from "@inertiajs/react";
import { useEffect, useRef, useState } from "react";
import CommandSearch from "./CommandSearch";
import Console from "./Console";
import NewEnvironment from "./NewEnvironment";
import StatusLights from "./StatusLights";
import { Icon } from "./icons";
import { Logo } from "./Logo";
import { Badge, Button, Field, Loading, Sheet, Status, Table, ToastProvider, useAction, when } from "./ui";
import { envPath, post, useApi } from "../lib/api";

/** The pages that live under Data, in the order the secondary navigation lists them. */
export const DATA_PAGES = [["resources", "Resources"], ["schemas", "Schemas"], ["transformers", "Transformers"], ["database", "SQL"]];

/** The secondary navigation shared by every Data page. */
export function dataSubnav(env, active) {
  return {
    title: "Data",
    groups: [{ items: DATA_PAGES.map(([key, label]) => ({ key, label, href: envHref(env, key) })) }],
    active,
  };
}

export const ENV_NAV = [
  { title: "Build", items: [
    ["overview", "Overview"], ["data", "Data", "resources", "database"], ["policies", "Policies"], ["routes", "Routes"], ["explorer", "API Explorer"], ["functions", "Functions"],
  ] },
  { title: "Automate", items: [
    ["flows", "Flows"], ["subscriptions", "Event subscriptions"], ["schedules", "Schedules"],
    ["webhooks", "Webhooks"], ["inbound-hooks", "Inbound hooks"],
  ] },
  { title: "Services", items: [["users", "Users & auth"], ["mail", "Mail"], ["storage", "Storage"], ["realtime", "Realtime"], ["status", "Status page"]] },
  { title: "Operate", items: [
    ["jobs", "Jobs & queues"], ["events", "Events & runs"], ["observability", "Observability"], ["usage", "Usage & limits"],
    ["keys", "API keys"], ["secrets", "Secrets"], ["backups", "Backups"], ["settings", "Settings"],
  ] },
];

// The pastel each section wears on its sheet headers and empty states.
export const SECTION_TONES = {
  resources: "lavender", schemas: "sky", transformers: "butter", policies: "mint", routes: "peach",
  functions: "sky", flows: "lavender", subscriptions: "rose", schedules: "butter", webhooks: "peach",
  "inbound-hooks": "mint", mail: "rose", buckets: "sky", storage: "sky",
};

const ENV_TONES = ["lavender", "peach", "mint", "butter", "sky", "rose"];

export function envTone(name, index = 0) {
  if (name === "production") return "peach";
  if (name === "development") return "mint";
  if (name === "staging") return "butter";
  return ENV_TONES[index % ENV_TONES.length];
}

export function envHref(env, section, child = "") {
  const path = section === "overview" ? `/envs/${env}` : `/envs/${env}/${section}${child ? `/${child}` : ""}`;
  // Keep branch context in navigation URLs as well as local storage. Server
  // rendered editors then receive the same working tree on their first load.
  try {
    const branch = localStorage.getItem(`pawabase.branch.${env}`) || "main";
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

function branchKey(env) {
  return `pawabase.branch.${env}`;
}

function BranchHistory({ runtime, env, url, onClose }) {
  const branches = useApi(envPath(env, "/branches"));
  const revisions = useApi(envPath(env, "/revisions"));
  const versions = useApi(envPath(env, "/api-versions"));
  const releases = useApi(envPath(env, "/releases"));
  const deployments = useApi(envPath(env, "/deployments"));
  const [branch, setBranch] = useState(() => {
    try { return localStorage.getItem(branchKey(env)) || "main"; } catch { return "main"; }
  });
  const [newBranch, setNewBranch] = useState("");
  const [target, setTarget] = useState("main");
  const [tab, setTab] = useState("branches");
  const [revisionMessage, setRevisionMessage] = useState("");
  const [newVersion, setNewVersion] = useState("");
  const [release, setRelease] = useState({ revision_id: "", api_version: "", name: "", allow_breaking: false });
  const [run, busy] = useAction();
  const branchDraft = useApi(branch === "main" ? null : envPath(env, `/branches/${branch}/draft`));
  useEffect(() => {
    const fromUrl = new URLSearchParams(url.split("?")[1] || "").get("branch");
    const next = fromUrl || (() => { try { return localStorage.getItem(branchKey(env)) || "main"; } catch { return "main"; } })();
    setBranch(next);
    try { localStorage.setItem(branchKey(env), next); } catch { /* session-only fallback */ }
  }, [env, url]);
  const choose = (next) => {
    setBranch(next);
    try { localStorage.setItem(branchKey(env), next); } catch { /* session-only fallback */ }
    window.dispatchEvent(new CustomEvent("pawabase:branch", { detail: { env, branch: next } }));
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
  const createRevision = async () => { if (await run(() => post(envPath(env, `/branches/${branch}/revisions`), { message: revisionMessage }), "Revision created")) { setRevisionMessage(""); reload(); } };
  const createVersion = async () => { if (await run(() => post(envPath(env, "/api-versions"), { name: newVersion }), "API version created")) { setNewVersion(""); reload(); } };
  const prepareRelease = async () => { if (await run(() => post(envPath(env, "/releases"), draft), "Release prepared")) { setRelease({ revision_id: "", api_version: "", name: "", allow_breaking: false }); reload(); } };
  return <Sheet title="History" subtitle={`Working on ${branch} · ${runtime.name} / ${env}`} icon="gitBranch" tabs={[["branches", "Branches"], ["releases", "Releases"], ["revisions", "Revisions"], ["versions", "API versions"], ["deployments", "Deployments"]].map(([value, label]) => ({ value, label }))} tab={tab} onTab={setTab} onClose={onClose} footer={<Button onClick={onClose}>Done</Button>}>
    {tab === "branches" && <>
    <section className="form-section">
      <div className="form-section-head"><div><h3>Checkout</h3><p>Switch the working definition tree instantly.</p></div></div>
      <div className="row wrap">
        {items.map((item) => <Button key={item.name} size="sm" variant={item.name === branch ? "primary" : ""} disabled={busy || item.name === branch} onClick={() => choose(item.name)}>{item.name}{item.protected ? " · protected" : ""}</Button>)}
      </div>
    </section>
    <section className="form-section">
      <div className="form-section-head"><div><h3>Create branch</h3><p>Starts from the checked-out branch, without changing it.</p></div></div>
      <div className="row"><input value={newBranch} onChange={(event) => setNewBranch(event.target.value)} placeholder="feature-checkout" /><Button variant="primary" disabled={busy || !newBranch} onClick={() => act(async () => { await post(envPath(env, "/branches"), { name: newBranch, from_branch: branch === "main" ? null : branch }); const next = newBranch; setNewBranch(""); choose(next); })}>Create & checkout</Button></div>
    </section>
    {branch !== "main" && <section className="form-section">
      <div className="form-section-head"><div><h3>Merge {branch}</h3><p>Applies this branch’s isolated definitions to a target branch.</p></div></div>
      <div className="row"><select value={target} onChange={(event) => setTarget(event.target.value)}>{items.filter((item) => item.name !== branch).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select><Button variant="primary" disabled={busy} onClick={() => act(() => post(envPath(env, `/branches/${branch}/merge`), { target }))}>Merge into {target}</Button></div>
      <div className="stack" style={{ gap: 6, marginTop: 12 }}><span className="hint">{branchDraft.data?.branch?.changes?.length || 0} recorded actions</span>{(branchDraft.data?.branch?.changes || []).slice().reverse().slice(0, 8).map((change, index) => <div className="hint" key={`${change.at || index}-${index}`}>{change.action}{change.target ? ` · ${change.target}` : ""}{change.at ? ` · ${when(change.at)}` : ""}</div>)}</div>
    </section>}
    </>}
    {tab === "releases" && <>
      <section className="form-section"><div className="form-section-head"><div><h3>Prepare release</h3><p>Point a stable API version at an immutable revision.</p></div></div><Field label="Revision"><select value={draft.revision_id} onChange={(event) => setRelease({ ...release, revision_id: event.target.value })}>{validRevisions.map((item) => <option key={item.id} value={item.id}>#{item.number} · {item.branch} · {item.message || item.checksum.slice(0, 8)}</option>)}</select></Field><Field label="API version"><select value={draft.api_version} onChange={(event) => setRelease({ ...release, api_version: event.target.value })}>{versionRows.map((item) => <option key={item.id}>{item.name}</option>)}</select></Field><Field label="Release name"><input value={release.name} onChange={(event) => setRelease({ ...release, name: event.target.value })} placeholder="v2.0.0" /></Field><label className="check"><input type="checkbox" checked={release.allow_breaking} onChange={(event) => setRelease({ ...release, allow_breaking: event.target.checked })} /> I acknowledge breaking compatibility findings.</label><div style={{ marginTop: 14 }}><Button variant="primary" disabled={busy || !draft.revision_id || !draft.api_version || !draft.name} onClick={prepareRelease}>Prepare release</Button></div></section>
      <section className="form-section"><div className="form-section-head"><div><h3>Prepared releases</h3></div></div><Loading state={releases} empty="No releases prepared.">{(data) => <Table rows={data.data} columns={[{ label: "Release", render: (item) => <b>{item.name}</b> }, { label: "Path", render: (item) => <code>/rest/{item.api_version}</code> }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Compatibility", render: (item) => item.compatibility?.compatible ? <Badge tone="green">compatible</Badge> : <Badge tone="red">breaking</Badge> }, { label: "Created", render: (item) => when(item.created_at) }, { label: "", render: (item) => <Button size="sm" disabled={busy || item.status === "active"} onClick={() => act(() => post(envPath(env, `/releases/${item.id}/activate`), {}))}>Activate</Button> }]} />}</Loading></section>
    </>}
    {tab === "revisions" && <><section className="form-section"><div className="form-section-head"><div><h3>Create revision</h3><p>Capture the current {branch} definition tree.</p></div></div><div className="row"><input value={revisionMessage} onChange={(event) => setRevisionMessage(event.target.value)} placeholder={`Snapshot ${branch}`} /><Button variant="primary" disabled={busy} onClick={createRevision}>Create revision</Button></div></section><Loading state={revisions} empty="No revisions yet.">{(data) => <Table rows={data.data} columns={[{ label: "#", key: "number" }, { label: "Branch", key: "branch" }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Message", key: "message" }, { label: "Created", render: (item) => when(item.created_at) }]} />}</Loading></>}
    {tab === "versions" && <><section className="form-section"><div className="form-section-head"><div><h3>Add API version</h3><p>Creates a stable public path such as v2.</p></div></div><div className="row"><input value={newVersion} onChange={(event) => setNewVersion(event.target.value)} placeholder="v2" /><Button variant="primary" disabled={busy || !/^v[1-9][0-9]*$/.test(newVersion)} onClick={createVersion}>Add version</Button></div></section><Loading state={versions} empty="No API versions yet.">{(data) => <Table rows={data.data} columns={[{ label: "Version", key: "name" }, { label: "Status", render: (item) => <Status value={item.status} /> }]} />}</Loading></>}
    {tab === "deployments" && <Loading state={deployments} empty="No deployments yet.">{(data) => <Table rows={data.data} columns={[{ label: "Version", key: "api_version" }, { label: "Action", key: "action" }, { label: "Release", render: (item) => <code>{item.release_id.slice(0, 8)}</code> }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "When", render: (item) => when(item.created_at) }, { label: "", render: (item) => item.id === data.data.find((row) => row.api_version === item.api_version)?.id && item.previous_release_id ? <Button size="sm" disabled={busy} onClick={() => act(() => post(envPath(env, `/api-versions/${item.api_version}/rollback`), {}))}>Rollback</Button> : null }]} />}</Loading>}
  </Sheet>;
}


const SIDEBAR_MODES = [
  ["expanded", "Expanded", "panelLeft", "Always show labels"],
  ["hover", "Expand on hover", "panelHover", "Icons only, labels on hover"],
  ["collapsed", "Collapsed", "panelClosed", "Icons only"],
];

function useSidebarMode() {
  const [mode, setMode] = useState(() => {
    try { return localStorage.getItem("pawabase.sidebar") || "expanded"; } catch { return "expanded"; }
  });
  const choose = (next) => {
    setMode(next);
    try { localStorage.setItem("pawabase.sidebar", next); } catch { /* the choice lasts for this page only */ }
  };
  return [mode, choose];
}

/** Closes a popover on Escape or a click outside it. */
function useDismiss(open, close, ref) {
  useEffect(() => {
    if (!open) return undefined;
    const key = (event) => { if (event.key === "Escape") close(); };
    const down = (event) => { if (ref.current && !ref.current.contains(event.target)) close(); };
    window.addEventListener("keydown", key);
    window.addEventListener("pointerdown", down);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("pointerdown", down); };
  }, [open, close, ref]);
}

function SidebarControl({ mode, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useDismiss(open, () => setOpen(false), ref);
  const current = SIDEBAR_MODES.find(([key]) => key === mode) || SIDEBAR_MODES[0];
  return (
    <div className="side-control" ref={ref}>
      {open && (
        <div className="side-menu" role="menu" aria-label="Sidebar control">
          <div className="side-menu-title">Sidebar control</div>
          {SIDEBAR_MODES.map(([key, label, icon, hint]) => (
            <button key={key} type="button" role="menuitemradio" aria-checked={key === mode} className={key === mode ? "on" : ""} onClick={() => { onChange(key); setOpen(false); }}>
              <Icon name={icon} size={16} />
              <span className="grow"><b>{label}</b><small>{hint}</small></span>
              {key === mode && <Icon name="check" size={15} />}
            </button>
          ))}
        </div>
      )}
      <button type="button" className="side-toggle" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)} title="Sidebar control">
        <Icon name={current[2]} size={18} />
        <span className="label">Sidebar</span>
      </button>
    </div>
  );
}

/** A second column of navigation for a section with parts of its own; the main sidebar folds to an icon rail while it is open. */
function SubNav({ subnav, backHref }) {
  const row = (item) => {
    const body = (
      <>
        {item.badge && <span className={`subnav-badge ${item.badgeTone || ""}`}>{item.badge}</span>}
        <span className="subnav-text">
          <span className={item.mono ? "mono" : ""}>{item.label}</span>
          {item.sub && <small>{item.sub}</small>}
        </span>
        {item.tag && <span className="subnav-tag">{item.tag}</span>}
        {item.flag && <i className={`obs-dot ${item.flag}`} />}
      </>
    );
    const active = subnav.active === item.key;
    return item.href ? (
      <Link key={item.key} href={item.href} className={`subnav-item ${active ? "active" : ""}`} aria-current={active ? "page" : undefined}>{body}{item.external && <Icon name="chevronRight" size={13} className="faint" />}</Link>
    ) : (
      <button key={item.key} type="button" className={`subnav-item ${active ? "active" : ""}`} aria-current={active ? "page" : undefined} title={item.title || item.label} onClick={() => subnav.onSelect(item.key)}>{body}</button>
    );
  };
  return (
    <aside className={`subnav ${subnav.wide ? "wide" : ""}`} aria-label={subnav.title}>
      <div className="subnav-head">
        <b>{subnav.title}</b>
        {subnav.action && <button type="button" className="subnav-action" onClick={subnav.action.onClick} title={subnav.action.label} aria-label={subnav.action.label}><Icon name="plus" size={16} /></button>}
      </div>
      {subnav.search && (
        <div className="subnav-search">
          <Icon name="search" size={14} />
          <input value={subnav.search.value} onChange={(event) => subnav.search.onChange(event.target.value)} placeholder={subnav.search.placeholder} aria-label={subnav.search.placeholder} />
        </div>
      )}
      <nav className="subnav-list">
        {subnav.loading && <p className="subnav-empty">Loading…</p>}
        {!subnav.loading && subnav.groups.every((group) => group.items.length === 0) && <p className="subnav-empty">{subnav.empty || "Nothing here yet."}</p>}
        {subnav.groups.map((group, index) => group.items.length > 0 && (
          <div key={group.title || index} className="subnav-group">
            {group.title && <div className="nav-title">{group.title}</div>}
            {group.items.map(row)}
          </div>
        ))}
      </nav>
      {subnav.note && <div className="subnav-note">{subnav.note}</div>}
      <Link href={backHref} className="subnav-foot"><Icon name="chevronRight" size={14} className="rotate-180" /> Overview</Link>
    </aside>
  );
}

/** The breadcrumb's project and environment, as one control that opens a wide menu: switch environment, create one, and the runtime-wide pages. */
function EnvMenu({ name, env, envs, section, open, setOpen, onHistory, onCreate }) {
  const ref = useRef(null);
  useDismiss(open, () => setOpen(false), ref);
  const index = Math.max(0, (envs || []).findIndex((e) => e.name === env));
  const go = (href) => { setOpen(false); router.visit(href); };
  const actions = [
    ["audit", "Audit log", "Who changed what, across the project", () => go("/audit")],
    ...(env ? [
      ["settings", "Environment settings", `Configuration for ${env}`, () => go(envHref(env, "settings"))],
      ["gitBranch", "Branches and history", "Checkout, merge and releases", () => { setOpen(false); onHistory(); }],
    ] : []),
  ];
  return (
    <div className="envmenu-wrap" ref={ref}>
      <button type="button" className="crumb-btn" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className={`avatar pastel ${envTone(env || "", index)}`}>{name.slice(0, 1).toUpperCase()}</span>
        <span>{name}</span>
        {env && <><span className="sep">/</span><span className="here">{env}</span></>}
        <Icon name="chevronDown" size={14} className="faint" />
      </button>
      {open && (
        <div className="envmenu" role="dialog" aria-label="Project and environment">
          <div className="envmenu-body">
            <section className="envmenu-col">
              <div className="envmenu-head"><span className="eyebrow">Project</span></div>
              <div className="envmenu-project">
                <span className="avatar pastel lavender">{name.slice(0, 1).toUpperCase()}</span>
                <div><b>{name}</b><small>This Studio manages one project</small></div>
              </div>
              <div className="envmenu-list">
                {actions.map(([icon, label, hint, onClick]) => (
                  <button key={label} type="button" className="envmenu-action" onClick={onClick}>
                    <Icon name={icon} size={16} />
                    <span className="grow"><b>{label}</b><small>{hint}</small></span>
                    <Icon name="chevronRight" size={14} className="faint" />
                  </button>
                ))}
              </div>
            </section>
            <section className="envmenu-col envmenu-envs">
              <div className="envmenu-head">
                <span className="eyebrow">Environments</span>
                <Button size="sm" variant="primary" onClick={() => { setOpen(false); onCreate(); }}><Icon name="plus" size={14} /> Create environment</Button>
              </div>
              <div className="envmenu-grid">
                {(envs || []).map((e, i) => (
                  <Link key={e.name} href={envHref(e.name, section || "overview")} className={`envmenu-item ${e.name === env ? "current" : ""}`} onClick={() => setOpen(false)}>
                    <span className={`avatar pastel ${envTone(e.name, i)}`}>{e.name.slice(0, 1).toUpperCase()}</span>
                    <span className="grow"><b>{e.name}</b><small>{e.name === env ? "Current environment" : e.is_default ? "Default environment" : e.version ? `Version ${e.version}` : "Switch to this"}</small></span>
                    {e.name === env ? <Icon name="check" size={15} /> : <Icon name="chevronRight" size={14} className="faint" />}
                  </Link>
                ))}
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Layout({ title, crumbs = [], children, full, subnav }) {
  const { props, url } = usePage();
  const { runtime, envs, env, section } = props;
  const [dark, toggleTheme] = useTheme();
  const [navOpen, setNavOpen] = useState(false);
  const [sidebar, setSidebar] = useSidebarMode();
  const [envMenu, setEnvMenu] = useState(false);
  const [newEnv, setNewEnv] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(() => new URLSearchParams(url.split("?")[1] || "").has("history"));
  useEffect(() => { setNavOpen(false); setEnvMenu(false); }, [url]);
  useEffect(() => setHistoryOpen(new URLSearchParams(url.split("?")[1] || "").has("history")), [url]);
  const closeHistory = () => {
    setHistoryOpen(false);
    const target = new URL(window.location.href);
    target.searchParams.delete("history");
    window.history.replaceState({}, "", `${target.pathname}${target.search}`);
  };
  const name = runtime?.name || "Pawabase";
  const envIndex = Math.max(0, (envs || []).findIndex((e) => e.name === env));
  return (
    <ToastProvider>
      <Head title={title} />
      <div className={`shell side-${subnav && sidebar === "expanded" ? "hover" : sidebar} ${subnav ? "has-sub" : ""} ${navOpen ? "nav-open" : ""}`}>
       <div className="frame">
        <div className="side-slot">
        <aside className="sidebar">
          <Link href="/" className="brand" title="Pawabase Studio"><Logo sub="Studio" /></Link>
          <nav className="nav">
            {env ? (
              ENV_NAV.map((group) => (
                <div key={group.title} className="nav-group">
                  <div className="nav-title">{group.title}</div>
                  <div className="nav-items">
                    {group.items.map(([key, label, to, icon]) => (
                      <Link key={key} href={envHref(env, to || key)} className={section === key || (key === "data" && DATA_PAGES.some(([page]) => page === section)) ? "active" : ""} title={label}>
                        <Icon name={icon || key} /><span className="label">{label}</span>
                      </Link>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <>
                <div className="nav-title">{name}</div>
                {(envs || []).map((e, i) => (
                  <Link key={e.name} href={envHref(e.name, "overview")}>
                    <span className={`avatar pastel ${envTone(e.name, i)}`} style={{ width: 18, height: 18, borderRadius: 6, fontSize: 10 }}>{e.name.slice(0, 1).toUpperCase()}</span>
                    <span className="label">{e.name}</span>
                  </Link>
                ))}
              </>
            )}
          </nav>
          <SidebarControl mode={sidebar} onChange={setSidebar} />
        </aside>
        </div>
        {subnav && <SubNav subnav={subnav} backHref={env ? envHref(env, "overview") : "/"} />}
        {navOpen && <div className="sheet-overlay" style={{ zIndex: 39 }} onClick={() => setNavOpen(false)} />}
        <div className="main-col">
        <main className="main">
          <div className="topbar">
            <div className="row" style={{ minWidth: 0 }}>
              <button type="button" className="icon-btn menu-btn" onClick={() => setNavOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
              <div className="crumbs">
                <EnvMenu name={name} env={env} envs={envs} section={section} open={envMenu} setOpen={setEnvMenu} onHistory={() => setHistoryOpen(true)} onCreate={() => setNewEnv(true)} />
                {crumbs.map((c, i) => <span key={i} className="row" style={{ gap: 4 }}><span className="sep">/</span><span className="here">{c}</span></span>)}
              </div>
            </div>
            <div className="top-actions">
              {env && <Button size="sm" onClick={() => setHistoryOpen(true)} title="Checkout, create, merge, and review branches"><Icon name="gitBranch" size={15} /> History</Button>}
              <StatusLights />
              <CommandSearch />
              <button type="button" className="icon-btn" onClick={toggleTheme} aria-label={dark ? "Switch to light" : "Switch to dark"}>
                <Icon name={dark ? "sun" : "moon"} />
              </button>
            </div>
          </div>
          <div className={`content ${full ? "full" : ""}`}>{children}</div>
        </main>
        <Console env={env} />
        </div>
       </div>
        {newEnv && <NewEnvironment envs={envs || []} onClose={() => setNewEnv(false)} onDone={(created) => router.visit(envHref(created, "overview"))} />}
        {historyOpen && env && <BranchHistory runtime={runtime || { name }} env={env} url={url} onClose={closeHistory} />}
      </div>
    </ToastProvider>
  );
}
