import { Head, Link, router, usePage } from "@inertiajs/react";
import { useEffect, useRef, useState } from "react";
import Console from "./Console";
import NewEnvironment from "./NewEnvironment";
import TopBar from "./TopBar";
import Tour from "./Tour";

import { Icon } from "./icons";
import { Badge, Button, Field, Loading, Sheet, Status, Table, ToastProvider, useAction, when } from "./ui";
import { envPath, post, useApi } from "../lib/api";

/** The sections that live under Data, for highlighting the top-level nav icon (the secondary sidebar itself comes from DataNav's useDataSubnav; kept in sync with its DATA_TOOLS by hand to avoid a circular import). */
const DATA_SECTIONS = ["tables", "database", "schemas", "transformers", "topology"];

/** The part of a page shown now, kept in ``?section=`` so a reload, a shared link and Back return to it. */
export function useSection(keys, fallback = Array.isArray(keys) ? keys[0] : undefined) {
  // ``keys`` is the list of sections, or a test for pages whose sections are their own records (``fn:checkout``).
  const valid = Array.isArray(keys) ? (value) => keys.includes(value) : keys;
  const read = () => {
    const value = new URLSearchParams(window.location.search).get("section");
    return value && valid(value) ? value : fallback;
  };
  const [section, setSection] = useState(read);
  useEffect(() => {
    const back = () => setSection(read());
    window.addEventListener("popstate", back);
    return () => window.removeEventListener("popstate", back);
  // keys are a constant list per page
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const choose = (next) => {
    setSection(next);
    const target = new URL(window.location.href);
    if (next === fallback) target.searchParams.delete("section");
    else target.searchParams.set("section", next);
    window.history.pushState(window.history.state, "", `${target.pathname}${target.search}`);
  };
  return [section, choose];
}

/** A secondary sidebar listing a page's sections: ``groups`` is ``[{ title?, pinned?, items: [[key, label, sub?, extra?], …] }]``.
 *  A ``pinned`` group (e.g. actions that shouldn't scroll with a long list above them) renders fixed at the
 *  bottom of the sidebar instead of in the scrollable list — see ``SubNav``. */
export function sectionNav(title, groups, active, onSelect, extra = {}) {
  return {
    title,
    groups: groups.map((group) => ({
      title: group.title,
      pinned: group.pinned,
      items: group.items.map(([key, label, sub, more]) => ({ key, label, sub, ...(more || {}) })),
    })),
    active,
    onSelect,
    ...extra,
  };
}

/** The pages under Webhooks: what goes out, what came of it, and what comes in. */
export const WEBHOOK_PAGES = [["webhooks", "Endpoints", "Outbound: events sent to your URLs"], ["webhook-deliveries", "Deliveries", "Every attempt, with retries"], ["inbound-hooks", "Inbound hooks", "URLs other services call"]];

/** The secondary navigation shared by every Webhooks page. */
export function webhooksSubnav(env, active) {
  return {
    title: "Webhooks",
    groups: [
      { title: "Outbound", items: WEBHOOK_PAGES.slice(0, 2).map(([key, label]) => ({ key, label, icon: key === "webhook-deliveries" ? "webhooks" : key, href: envHref(env, key) })) },
      { title: "Inbound", items: WEBHOOK_PAGES.slice(2).map(([key, label]) => ({ key, label, icon: key, href: envHref(env, key) })) },
      { pinned: true, items: [{ key: "events", label: "Events & runs", icon: "events", href: envHref(env, "events"), external: true }] },
    ],
    active,
  };
}

export const ENV_NAV = [
  { items: [["overview", "Overview"]] },
  { title: "Data", items: [["data", "Data", "tables", "database"], ["explorer", "API Explorer"]] },
  { title: "Access", items: [["users", "Users & auth"], ["policies", "Policies"], ["keys", "API keys"], ["secrets", "Secrets"], ["networking", "Networking"]] },
  { title: "Logic", items: [["functions", "Functions"], ["flows", "Flows"], ["routes", "Routes"], ["schedules", "Schedules"], ["subscriptions", "Event subscriptions"], ["webhooks", "Webhooks"]] },
  { title: "Services", items: [["mail", "Mail"], ["storage", "Storage"], ["realtime", "Realtime"]] },
  { title: "Activity", items: [["activity", "Activity"]] },
  { title: "Project", items: [["usage", "Usage & plan"], ["backups", "Backups"], ["status", "Status page"], ["settings", "Settings"]] },
];

// Pages that were folded into Activity still highlight it when reached from an old link.
const ACTIVITY_SECTIONS = ["activity", "observability", "jobs", "events"];

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
  return <Sheet title="History" subtitle={`Working on ${branch} · ${runtime.name} / ${env}`} icon="gitBranch" tabs={[["branches", "Branches"], ["releases", "Releases"], ["revisions", "Revisions"], ["versions", "API versions"], ["deployments", "Deployments"]].map(([value, label]) => ({ value, label }))} tab={tab} onTab={setTab} tabsPlacement="top" onClose={onClose} footer={<Button onClick={onClose}>Done</Button>}>
    {tab === "branches" && <>
    <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
      <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Checkout</h3><p>Switch the working definition tree instantly.</p></div></div>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
        {items.map((item) => <Button key={item.name} size="sm" variant={item.name === branch ? "primary" : ""} disabled={busy || item.name === branch} onClick={() => choose(item.name)}>{item.name}{item.protected ? " · protected" : ""}</Button>)}
      </div>
    </section>
    <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
      <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Create branch</h3><p>Starts from the checked-out branch, without changing it.</p></div></div>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><input value={newBranch} onChange={(event) => setNewBranch(event.target.value)} placeholder="feature-checkout" /><Button variant="primary" disabled={busy || !newBranch} onClick={() => act(async () => { await post(envPath(env, "/branches"), { name: newBranch, from_branch: branch === "main" ? null : branch }); const next = newBranch; setNewBranch(""); choose(next); })}>Create & checkout</Button></div>
    </section>
    {branch !== "main" && <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
      <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Merge {branch}</h3><p>Applies this branch’s isolated definitions to a target branch.</p></div></div>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><select value={target} onChange={(event) => setTarget(event.target.value)}>{items.filter((item) => item.name !== branch).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select><Button variant="primary" disabled={busy} onClick={() => act(() => post(envPath(env, `/branches/${branch}/merge`), { target }))}>Merge into {target}</Button></div>
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 6, marginTop: 12 }}><span className="hint text-xs font-book text-muted leading-[1.45]">{branchDraft.data?.branch?.changes?.length || 0} recorded actions</span>{(branchDraft.data?.branch?.changes || []).slice().reverse().slice(0, 8).map((change, index) => <div className="hint text-xs font-book text-muted leading-[1.45]" key={`${change.at || index}-${index}`}>{change.action}{change.target ? ` · ${change.target}` : ""}{change.at ? ` · ${when(change.at)}` : ""}</div>)}</div>
    </section>}
    </>}
    {tab === "releases" && <>
      <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0"><div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Prepare release</h3><p>Point a stable API version at an immutable revision.</p></div></div><Field label="Revision"><select value={draft.revision_id} onChange={(event) => setRelease({ ...release, revision_id: event.target.value })}>{validRevisions.map((item) => <option key={item.id} value={item.id}>#{item.number} · {item.branch} · {item.message || item.checksum.slice(0, 8)}</option>)}</select></Field><Field label="API version"><select value={draft.api_version} onChange={(event) => setRelease({ ...release, api_version: event.target.value })}>{versionRows.map((item) => <option key={item.id}>{item.name}</option>)}</select></Field><Field label="Release name"><input value={release.name} onChange={(event) => setRelease({ ...release, name: event.target.value })} placeholder="v2.0.0" /></Field><label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui"><input type="checkbox" checked={release.allow_breaking} onChange={(event) => setRelease({ ...release, allow_breaking: event.target.checked })} /> I acknowledge breaking compatibility findings.</label><div style={{ marginTop: 14 }}><Button variant="primary" disabled={busy || !draft.revision_id || !draft.api_version || !draft.name} onClick={prepareRelease}>Prepare release</Button></div></section>
      <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0"><div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Prepared releases</h3></div></div><Loading state={releases} empty="No releases prepared.">{(data) => <Table rows={data.data} columns={[{ label: "Release", render: (item) => <b>{item.name}</b> }, { label: "Path", render: (item) => <code>/rest/{item.api_version}</code> }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Compatibility", render: (item) => item.compatibility?.compatible ? <Badge tone="green">compatible</Badge> : <Badge tone="red">breaking</Badge> }, { label: "Created", render: (item) => when(item.created_at) }, { label: "", render: (item) => <Button size="sm" disabled={busy || item.status === "active"} onClick={() => act(() => post(envPath(env, `/releases/${item.id}/activate`), {}))}>Activate</Button> }]} />}</Loading></section>
    </>}
    {tab === "revisions" && <><section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0"><div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Create revision</h3><p>Capture the current {branch} definition tree.</p></div></div><div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><input value={revisionMessage} onChange={(event) => setRevisionMessage(event.target.value)} placeholder={`Snapshot ${branch}`} /><Button variant="primary" disabled={busy} onClick={createRevision}>Create revision</Button></div></section><Loading state={revisions} empty="No revisions yet.">{(data) => <Table rows={data.data} columns={[{ label: "#", key: "number" }, { label: "Branch", key: "branch" }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Message", key: "message" }, { label: "Created", render: (item) => when(item.created_at) }]} />}</Loading></>}
    {tab === "versions" && <><section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0"><div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Add API version</h3><p>Creates a stable public path such as v2.</p></div></div><div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><input value={newVersion} onChange={(event) => setNewVersion(event.target.value)} placeholder="v2" /><Button variant="primary" disabled={busy || !/^v[1-9][0-9]*$/.test(newVersion)} onClick={createVersion}>Add version</Button></div></section><Loading state={versions} empty="No API versions yet.">{(data) => <Table rows={data.data} columns={[{ label: "Version", key: "name" }, { label: "Status", render: (item) => <Status value={item.status} /> }]} />}</Loading></>}
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
    <div className="side-control relative mt-2.5 pt-2.5 [border-top:1px_solid_var(--line-2)] max-navigation:hidden h-[var(--dock)] box-border m-0 p-[0_14px] hidden items-center" ref={ref}>
      {open && (
        <div className="side-menu absolute left-0 bottom-[calc(100%_+_6px)] w-[262px] bg-surface [border:1px_solid_var(--line-2)] rounded-sm p-1.5 z-[40] [&_button]:flex [&_button]:items-center [&_button]:gap-2.5 [&_button]:w-full [&_button]:p-[8px_10px] [&_button]:border-0 [&_button]:rounded-xs [&_button]:bg-none [&_button]:text-text [&_button]:font-inherit [&_button]:text-left [&_button]:cursor-pointer [&_button:hover]:bg-sunken [&_button.on]:bg-sunken [&_b]:block [&_b]:text-body [&_b]:font-semibold [&_small]:block [&_small]:text-xs [&_small]:text-muted min-w-[262px] shadow-none" role="menu" aria-label="Sidebar control">
          <div className="side-menu-title p-[8px_10px_6px] text-2xs font-bold uppercase tracking-[0.09em] text-faint">Sidebar control</div>
          {SIDEBAR_MODES.map(([key, label, icon, hint]) => (
            <button key={key} type="button" role="menuitemradio" aria-checked={key === mode} className={key === mode ? "on" : ""} onClick={() => { onChange(key); setOpen(false); }}>
              <Icon name={icon} size={16} />
              <span className="grow flex-1 min-w-0"><b>{label}</b><small>{hint}</small></span>
              {key === mode && <Icon name="check" size={15} />}
            </button>
          ))}
        </div>
      )}
      <button type="button" className="side-toggle flex items-center gap-[11px] w-full p-[6px_12px] border-0 rounded-sm bg-none text-muted font-inherit font-medium-plus text-base cursor-pointer [&:hover]:bg-sunken [&:hover]:text-text [&[aria-expanded=true]]:bg-sunken [&[aria-expanded=true]]:text-text" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)} title="Sidebar control">
        <Icon name={current[2]} size={18} />
        <span className="label whitespace-nowrap">Sidebar</span>
      </button>
    </div>
  );
}

/** Folders in the secondary sidebar: ``tree`` is ``[{ key, title, icon?, folders?: [{ key, title, items }], items? }]``.
 *  A folder opens on click; the one holding the active item, and every folder while searching, opens on its own. */
function SubNavTree({ tree, active, forceOpen, row }) {
  const [open, setOpen] = useState(() => new Set());
  const holds = (node) => (node.items || []).some((item) => item.key === active) || (node.folders || []).some(holds);
  useEffect(() => {
    if (!active) return;
    setOpen((current) => {
      const next = new Set(current);
      for (const node of tree) {
        if (holds(node)) next.add(node.key);
        for (const folder of node.folders || []) if (holds(folder)) next.add(folder.key);
      }
      return next;
    });
  // reopen when the active item changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
  const toggle = (key) => setOpen((current) => { const next = new Set(current); next.has(key) ? next.delete(key) : next.add(key); return next; });
  const count = (node) => (node.items || []).length + (node.folders || []).reduce((sum, folder) => sum + count(folder), 0);
  const folder = (node, depth) => {
    const isOpen = forceOpen || open.has(node.key);
    return (
      <div key={node.key} className={`subnav-folder [&.depth-1_>_.subnav-folder-head]:font-medium-plus [&.depth-1_>_.subnav-folder-head]:text-muted [&.depth-1_>_.subnav-folder-head]:text-ui-sm [&.open_>_.subnav-folder-head_.subnav-chevron]:transform-[rotate(90deg)] depth-${depth} ${isOpen ? "open" : ""}`}>
        <button type="button" className="subnav-folder-head flex items-center gap-[7px] w-full min-h-8 p-[6px_8px] border-0 rounded-sm bg-none text-text font-inherit text-ui font-semibold text-left cursor-pointer [&:hover]:bg-hover" aria-expanded={isOpen} onClick={() => toggle(node.key)}>
          <Icon name="chevronRight" size={13} className="subnav-chevron flex-none text-faint transition-[transform_0.12s_ease]" />
          {node.icon && <Icon name={node.icon} size={14} className="faint text-faint" />}
          <span className="grow flex-1 min-w-0">{node.title}</span>
          <span className="subnav-count text-2xs font-semibold text-faint">{count(node)}</span>
        </button>
        {isOpen && (
          <div className="subnav-folder-body flex flex-col gap-px pl-3 ml-[13px] [border-left:1px_solid_var(--line-2)]">
            {(node.folders || []).map((child) => folder(child, depth + 1))}
            {(node.items || []).map(row)}
          </div>
        )}
      </div>
    );
  };
  return <div className="subnav-tree flex flex-col gap-px">{tree.map((node) => folder(node, 0))}</div>;
}

/** A second column of navigation for a section with parts of its own; the main sidebar folds to an icon rail while it is open. */
function SubNav({ subnav, backHref, showHeader = true }) {
  const row = (item) => {
    const body = (
      <>
        {item.icon && <Icon name={item.icon} size={15} className="subnav-icon shrink-0 text-faint" />}
        {item.badge && <span className={`subnav-badge font-mono text-3xs font-bold p-[1px_6px] rounded-xs bg-sunken text-muted [&.blue]:bg-info-soft [&.blue]:text-info [&.green]:bg-ok-soft [&.green]:text-ok [&.yellow]:bg-warn-soft [&.yellow]:text-warn [&.red]:bg-danger-soft [&.red]:text-danger min-w-[42px] text-center ${item.badgeTone || ""}`}>{item.badge}</span>}
        <span className="subnav-text flex-1 min-w-0 grid gap-px [&_>_span]:overflow-hidden [&_>_span]:text-ellipsis [&_>_span]:whitespace-nowrap [&_small]:text-caption [&_small]:font-book [&_small]:text-faint [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap">
          <span className={item.mono ? "mono" : ""}>{item.label}</span>
        </span>
        {item.tag && <span className="subnav-tag text-[10.5px] text-faint rounded-xs">{item.tag}</span>}
        {item.flag && <i className={`obs-dot w-2 h-2 rounded-full bg-warn flex-none [&.critical]:bg-danger ${item.flag}`} />}
      </>
    );
    const active = subnav.active === item.key;
    return item.href ? (
      <Link key={item.key} data-tour={`sub-${item.key}`} href={item.href} className={`subnav-item [&.active_.subnav-icon]:text-brand relative flex items-center gap-2 w-full min-h-8 p-[6px_10px] border-0 rounded-sm bg-none text-muted font-inherit text-body font-medium text-left cursor-pointer [&:hover]:bg-hover [&:hover]:text-text [&.active]:bg-sunken [&.active]:text-text [&.active]:font-semibold h-auto [&.active]:shadow-none [&.active::before]:hidden [&.active_.icon]:text-brand ${active ? "active" : ""}`} aria-current={active ? "page" : undefined}>{body}{item.external && <Icon name="chevronRight" size={13} className="faint text-faint" />}</Link>
    ) : (
      <button key={item.key} type="button" className={`subnav-item [&.active_.subnav-icon]:text-brand relative flex items-center gap-2 w-full min-h-8 p-[6px_10px] border-0 rounded-sm bg-none text-muted font-inherit text-body font-medium text-left cursor-pointer [&:hover]:bg-hover [&:hover]:text-text [&.active]:bg-sunken [&.active]:text-text [&.active]:font-semibold h-auto [&.active]:shadow-none [&.active::before]:hidden [&.active_.icon]:text-brand ${active ? "active" : ""}`} aria-current={active ? "page" : undefined} title={item.title || item.label} onClick={() => subnav.onSelect(item.key)}>{body}</button>
    );
  };
  return (
    <aside className={`subnav w-[248px] shrink-0 flex flex-col bg-canvas [border-right:1px_solid_var(--line)] z-[20] max-navigation:hidden [&.wide]:w-[320px] h-full min-h-0 ${subnav.wide ? "wide" : ""}`} aria-label={subnav.title}>
      {showHeader && <div className="subnav-head h-[var(--bar)] box-border flex items-center p-[0_12px_0_16px] [border-bottom:1px_solid_var(--line)] text-base justify-between [&_b]:font-semibold">
        <b>{subnav.title}</b>
        {subnav.action && <button type="button" className="subnav-action grid place-items-center w-control-sm h-control-sm [border:1px_solid_var(--line-2)] rounded-sm bg-transparent text-text cursor-pointer [&:hover]:bg-hover" onClick={subnav.action.onClick} title={subnav.action.label} aria-label={subnav.action.label}><Icon name="plus" size={16} /></button>}
      </div>}
      {subnav.search && (
        <div className="subnav-search flex items-center gap-2 m-[12px_12px_4px] p-[0_10px] [border:1px_solid_var(--line-2)] rounded-sm bg-transparent text-faint [&_input]:flex-1 [&_input]:min-w-0 [&_input]:border-0 [&_input]:outline-none [&_input]:shadow-none [&_input]:bg-transparent [&_input]:p-[8px_0] [&_input]:text-ui [&:focus-within]:border-brand h-control">
          <Icon name="search" size={14} />
          <input value={subnav.search.value} onChange={(event) => subnav.search.onChange(event.target.value)} placeholder={subnav.search.placeholder} aria-label={subnav.search.placeholder} />
        </div>
      )}
      {subnav.create && <button type="button" className="subnav-create flex items-center justify-center gap-1.5 m-[0_12px_8px] h-control [border:1px_solid_var(--line-2)] rounded-sm bg-transparent text-muted font-inherit text-ui-sm font-semibold cursor-pointer [&:hover]:bg-hover [&:hover]:text-text" onClick={subnav.create.onClick}><Icon name="plus" size={14} />{subnav.create.label}</button>}
      <nav className="subnav-list flex-1 overflow-y-auto p-[8px_10px] [&_button]:relative [&_button]:flex [&_button]:items-center [&_button]:gap-2 [&_button]:w-full [&_button]:h-auto [&_button]:p-[6px_10px] [&_button]:border-0 [&_button]:rounded-sm [&_button]:bg-none [&_button]:text-muted [&_button]:font-inherit [&_button]:text-body [&_button]:font-medium [&_button]:text-left [&_button]:cursor-pointer [&_button:hover]:bg-hover [&_button:hover]:text-text [&_button.active]:bg-sunken [&_button.active]:text-text [&_button.active]:font-semibold [&_.subnav-item]:h-auto [&_.subnav-item]:p-[7px_10px] [&_.subnav-item_.subnav-text_small]:mt-px min-h-0 [&_button]:min-h-8 [&_button.active]:shadow-none [&_button.active::before]:hidden [&_.subnav-group]:m-[0_-10px] [&_.subnav-group]:p-[4px_10px_8px] [&_.subnav-group_+_.subnav-group]:mt-0 [&_.subnav-group_+_.subnav-group]:[border-top:1px_solid_var(--line-2)] [&_.subnav-group_+_.subnav-group]:pt-1.5 [&_.subnav-group_>_.nav-title]:p-[10px_10px_6px] [&_.subnav-group_>_.nav-title]:text-2xs [&_.subnav-group_>_.nav-title]:font-semibold-plus [&_.subnav-group_>_.nav-title]:tracking-[0.06em] [&_.subnav-group_>_.nav-title]:uppercase [&_.subnav-group_>_.nav-title]:text-muted">
        {subnav.loading && <p className="subnav-empty m-[16px_10px] text-muted text-ui">Loading…</p>}
        {subnav.tree && !subnav.loading && subnav.tree.length === 0 && <p className="subnav-empty m-[16px_10px] text-muted text-ui">{subnav.empty || "Nothing here yet."}</p>}
        {subnav.tree && <SubNavTree tree={subnav.tree} active={subnav.active} forceOpen={subnav.expandAll} row={row} />}
        {!subnav.tree && !subnav.loading && subnav.groups.every((group) => group.items.length === 0) && <p className="subnav-empty m-[16px_10px] text-muted text-ui">{subnav.empty || "Nothing here yet."}</p>}
        {!subnav.tree && subnav.groups.filter((g) => !g.pinned).map((group, index) => (group.items.length > 0 || group.empty) && (
          <div key={group.title || index} className="subnav-group [&_+_.subnav-group]:mt-1.5 [&_.nav-title]:p-[12px_10px_6px] [&_.nav-title]:text-2xs [&_.nav-title]:font-bold [&_.nav-title]:uppercase [&_.nav-title]:tracking-[0.1em] [&_.nav-title]:text-faint">
            {group.title && <div className="nav-title text-2xs font-semibold uppercase tracking-[0.08em] text-faint p-[16px_10px_6px] [&::after]:hidden">{group.title}</div>}
            {group.items.map(row)}
            {group.items.length === 0 && group.empty && <p className="subnav-empty m-[2px_10px_10px] text-muted text-ui-sm">{group.empty}</p>}
          </div>
        ))}
      </nav>
      {subnav.note && <div className="subnav-note p-[10px_22px] [border-top:1px_solid_var(--line)] text-muted text-xs">{subnav.note}</div>}
      {!subnav.tree && subnav.groups.filter((g) => g.pinned && g.items.length > 0).map((group, index) => (
        <div key={group.title || `pinned-${index}`} className="subnav-pinned shrink-0 p-[6px_12px_10px] [border-top:1px_solid_var(--line-2)]">
          {group.title && <div className="nav-title text-2xs font-semibold uppercase tracking-[0.08em] text-faint p-[16px_10px_6px] [&::after]:hidden">{group.title}</div>}
          {group.items.map(row)}
        </div>
      ))}
      <Link href={backHref} className="subnav-foot h-[44px] box-border flex items-center gap-2 p-[0_16px] [border-top:1px_solid_var(--line)] text-muted text-ui-sm font-semibold [&:hover]:text-text"><Icon name="chevronRight" size={14} className="rotate-180 transform-[rotate(180deg)]" /> Overview</Link>
    </aside>
  );
}

export default function Layout({ title, crumbs = [], children, full, subnav, flowPage = false }) {
  const { props, url } = usePage();
  const { runtime, envs, env, section, confined } = props;
  const [navOpen, setNavOpen] = useState(false);
  const [sidebar, setSidebar] = useSidebarMode();
  const sidebarBeforeFlow = useRef(sidebar);
  const [newEnv, setNewEnv] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(() => new URLSearchParams(url.split("?")[1] || "").has("history"));
  useEffect(() => { setNavOpen(false); }, [url]);
  useEffect(() => {
    if (!flowPage) return undefined;
    sidebarBeforeFlow.current = sidebar;
    if (sidebar !== "collapsed") setSidebar("collapsed");
    return () => {
      if (sidebarBeforeFlow.current !== "collapsed") setSidebar(sidebarBeforeFlow.current);
    };
  // The editor owns this temporary navigation mode for its lifetime.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowPage]);
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
      <div className={`shell h-full p-0 max-navigation:p-0 max-navigation:gap-0 max-navigation:[&.nav-open_.sidebar]:transform-[none] [&_.term-in_input]:min-h-0 [&_.term-in_input]:h-auto [&_.term-in_input]:border-0 [&_.term-in_input]:rounded-none [&_.term-in_input]:p-0 [&_.term-in_input]:bg-transparent [&_.term-in_input]:shadow-none [&_.term-in_input:focus]:border-0 [&_.term-in_input:focus]:shadow-none [&_.term-find_input]:min-h-0 [&_.term-find_input]:h-auto [&_.term-find_input]:[border:1px_solid_var(--term-line)] [&_.term-find_input]:rounded-xs [&_.term-find_input]:p-[3px_8px] [&_.term-find_input]:bg-[#3c3c3c] [&_.term-find_input]:text-[var(--term-text)] [&_.term-find_input]:shadow-none [&_.term-find_input:focus]:[border-color:#007fd4] [&_.term-find_input:focus]:shadow-none [&.flow-page_.main.flow-main]:min-w-0 [&.flow-page_.main.flow-main]:min-h-0 [&.flow-page_.main.flow-main]:overflow-hidden [&.flow-page_.content.flow-content]:flex-1 [&.flow-page_.content.flow-content]:w-full [&.flow-page_.content.flow-content]:min-w-0 [&.flow-page_.content.flow-content]:min-h-0 [&.flow-page_.content.flow-content]:h-auto [&.flow-page_.content.flow-content]:max-w-none [&.flow-page_.content.flow-content]:p-0 [&.flow-page_.content.flow-content]:overflow-hidden [&.flow-page_.flow-shell]:w-full [&.flow-page_.flow-shell]:h-full [&.flow-page_.flow-shell]:min-w-0 [&.flow-page_.flow-shell]:min-h-0 [&.flow-page_.flow-shell]:overflow-hidden [&.flow-page_.palette]:min-w-0 [&.flow-page_.palette]:min-h-0 [&.flow-page_.palette]:overflow-x-hidden [&.flow-page_.palette]:overflow-y-auto [&.flow-page_.palette]:overscroll-contain [&.flow-page_.inspector]:min-w-0 [&.flow-page_.inspector]:min-h-0 [&.flow-page_.inspector]:overflow-x-hidden [&.flow-page_.inspector]:overflow-y-auto [&.flow-page_.inspector]:overscroll-contain [&.flow-page_.flow-canvas]:relative [&.flow-page_.flow-canvas]:min-w-0 [&.flow-page_.flow-canvas]:min-h-0 [&.flow-page_.flow-canvas]:overflow-hidden [&.flow-page_.flow-canvas]:bg-canvas [&.flow-page_.flow-canvas_>_.react-flow]:absolute [&.flow-page_.flow-canvas_>_.react-flow]:inset-[44px_0_0_0] [&.flow-page_.inspector-tabs]:p-[8px_12px_0] [&.flow-page_.inspector-body]:p-[0_14px_20px] grid grid-rows-[var(--bar)_minmax(0,_1fr)] bg-canvas [&_>_.frame]:h-auto [&_>_.frame]:min-h-0 [&_>_.frame]:bg-canvas [&:not(.side-collapsed):not(.side-hover)_.appbar-brand]:w-auto [&:not(.side-collapsed):not(.side-hover)_.appbar-brand]:justify-start [&:not(.side-collapsed):not(.side-hover)_.appbar-brand]:pl-[18px] [&_.content]:p-0 [&_.content]:max-w-none [&_.content]:flex [&_.content]:flex-col [&_.content]:min-h-[100%] [&_.content.full]:h-full [&_.content.full]:min-h-0 [&_.content_>_*]:mt-0! [&_.content_>_*]:mb-0 [&_.content_>_.page-head]:order-[-2] [&_.content_>_.page-head]:flex [&_.content_>_.page-head]:items-center [&_.content_>_.page-head]:justify-between [&_.content_>_.page-head]:gap-4 [&_.content_>_.page-head]:min-h-[72px] [&_.content_>_.page-head]:m-0 [&_.content_>_.page-head]:p-[16px_var(--gutter)] [&_.content_>_.page-head]:[border-bottom:1px_solid_var(--line)] [&_.content_>_.card]:m-0 [&_.content_>_.card]:border-0 [&_.content_>_.card]:rounded-none [&_.content_>_.card]:[border-bottom:1px_solid_var(--line)] [&_.content_>_.card]:bg-transparent [&_.content_>_.card]:p-[20px_var(--gutter)] [&_.content_>_.form-section]:m-0 [&_.content_>_.form-section]:border-0 [&_.content_>_.form-section]:rounded-none [&_.content_>_.form-section]:[border-bottom:1px_solid_var(--line)] [&_.content_>_.form-section]:bg-transparent [&_.content_>_.form-section]:p-[20px_var(--gutter)] [&_.content_>_section]:m-0 [&_.content_>_section]:border-0 [&_.content_>_section]:rounded-none [&_.content_>_section]:[border-bottom:1px_solid_var(--line)] [&_.content_>_section]:bg-transparent [&_.content_>_section]:p-[20px_var(--gutter)] [&_.content_>_.card.flush]:p-0 [&_.content_>_.card.flush_>_.card-head]:p-[0_var(--gutter)] [&_.content_>_.alert]:m-[12px_var(--gutter)_0] [&_.content_>_:where(.stack,_.grid,_.grid2,_.row,_.spread,_.obs-bar,_.segmented,_.usage-filters,_.split,_div:not([class]))]:p-[16px_var(--gutter)] [&_.content_>_:where(.stack,_.grid,_.grid2,_.obs-split)]:[border-bottom:1px_solid_var(--line)] [&_.content_>_.obs-split]:p-[16px_var(--gutter)] [&_.content_>_.tabs]:p-[0_var(--gutter)] [&_.content_>_.tabs]:m-0 [&_.content_>_.card.flush_th:first-child]:pl-gutter [&_.content_>_.card.flush_td:first-child]:pl-gutter [&_.content_>_.table-wrap_th:first-child]:pl-gutter [&_.content_>_.table-wrap_td:first-child]:pl-gutter [&_.content_>_.card.flush_th:last-child]:pr-gutter [&_.content_>_.card.flush_td:last-child]:pr-gutter [&_.content_>_.table-wrap_th:last-child]:pr-gutter [&_.content_>_.table-wrap_td:last-child]:pr-gutter [&_.content_>_.spread_.chips]:gap-0 [&_.content_>_.spread_.chips]:flex-nowrap [&_.content_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_.content_>_.spread_.chips]:rounded-sm [&_.content_>_.spread_.chips]:overflow-hidden [&_.content_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_.content_>_.spread_.chip]:p-[0_10px] [&_.content_>_.spread_.chip]:border-0 [&_.content_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_.content_>_.spread_.chip]:rounded-none [&_.content_>_.spread_.chip]:bg-transparent [&_.content_>_.spread_.chip]:text-muted [&_.content_>_.spread_.chip]:text-xs [&_.content_>_.spread_.chip]:font-medium-plus [&_.content_>_.spread_.chip:first-child]:border-l-0 [&_.content_>_.ov-grid]:p-0 [&:not(.side-collapsed):not(.side-hover)_.appbar-brand]:h-auto [&:not(.side-collapsed):not(.side-hover)_.appbar-brand]:p-[0_6px_0_0] [&:not(.side-collapsed):not(.side-hover)_.appbar-brand]:border-r-0 grid-cols-[minmax(0,_1fr)] w-full overflow-clip [&_>_.frame]:min-w-0 [&_.content_>_.page-head]:flex-wrap [&_.content_>_.page-head]:gap-y-2.5 [&_.content_>_.ex-context]:m-0 [&_.content_>_.ex-context]:p-[18px_var(--gutter)_16px] [&_.content_>_.ex-context]:[border-bottom:1px_solid_var(--line)] [&_.content_>_.fw-flat]:p-0 [&_.content_>_.fw-flat]:grid [&_.content_>_.fw-flat]:[border-bottom:1px_solid_var(--line)] [&_.content_>_.ov-board]:order-[0] [&_.content_>_.ov-board]:m-[16px_var(--gutter)]! [&_.content_>_.ov-board]:[border:1px_solid_var(--line-2)] [&_.content_>_.ov-board]:rounded-[calc(var(--r)_+_4px)] [&_.content_>_.ov-board]:overflow-hidden [&_.content_>_.ov-board]:bg-transparent [&_.content_>_.ov-board]:shadow-none side-${subnav && sidebar === "expanded" ? "hover" : sidebar} ${subnav ? "has-sub" : ""} ${navOpen ? "nav-open" : ""} ${flowPage ? "flow-page" : ""}`}>
       <TopBar runtime={runtime} envs={envs} env={env} section={section} confined={confined} crumbs={crumbs} onMenu={() => setNavOpen(true)} onHistory={() => setHistoryOpen(true)} onCreateEnv={() => setNewEnv(true)} />
       <div className="frame relative flex h-full bg-canvas">
        <div className="side-slot relative w-[236px] shrink-0 transition-[width_0.18s_ease] z-[30] max-navigation:w-0">
        <button type="button" className="side-collapse absolute z-[40] grid place-items-center cursor-pointer text-muted top-[50%] right-[-12px] w-6 h-6 transform-[translateY(-50%)] [border:1px_solid_var(--line-2)] rounded-sm bg-canvas shadow-none" onClick={() => setSidebar(sidebar === "expanded" ? "collapsed" : "expanded")} aria-label={sidebar === "expanded" ? "Collapse the sidebar" : "Expand the sidebar"} title={sidebar === "expanded" ? "Collapse" : "Expand"}><Icon name="chevronRight" size={14} style={{ transform: sidebar === "expanded" ? "rotate(180deg)" : "none" }} /></button>
        <aside className="sidebar w-[236px] shrink-0 bg-canvas rounded-none flex flex-col min-h-0 p-[8px_0_12px] max-navigation:fixed max-navigation:inset-[0_auto_0_0] max-navigation:z-[40] max-navigation:shadow-none max-navigation:transform-[translateX(-120%)] max-navigation:transition-[transform_0.25s_ease] absolute inset-[0_auto_0_0] [border-right:1px_solid_var(--line)] transition-[width_0.18s_ease] [&_.brand]:pb-3.5 [&_.brand]:[border-bottom:1px_solid_var(--line-2)] [&_.brand]:mb-2.5 max-navigation:w-[292px]! max-navigation:px-3.5! max-navigation:[&_.label]:[display:revert]! max-navigation:[&_.brand-name]:[display:revert]! max-navigation:[&_.nav-title]:[display:revert]! overflow-visible [&_.nav]:overflow-y-auto [&_.nav]:overflow-x-hidden [&_.brand]:h-[var(--bar)] [&_.brand]:box-border [&_.brand]:m-0 [&_.brand]:p-[0_22px] [&_.brand]:items-center [&_.nav]:m-0 [&_.nav]:p-[0_10px] max-navigation:[border-right:1px_solid_var(--line-2)] [&_.brand]:hidden [&::after]:hidden [&::before]:hidden">
          <nav className="nav overflow-auto flex-1 m-[0_-4px] p-[0_4px] [&_a]:flex [&_a]:items-center [&_a]:gap-2.5 [&_a]:p-[0_10px] [&_a]:rounded-sm [&_a]:text-muted [&_a]:font-medium [&_a]:text-body [&_a]:transition-[background_0.15s,_color_0.15s] [&_a_+_a]:mt-px [&_a:hover]:bg-hover [&_a:hover]:text-text [&_a.active]:bg-sunken [&_a.active]:text-text [&_a.active]:font-semibold [&_a_.icon]:opacity-100 [&_a_.grow]:overflow-hidden [&_a_.grow]:text-ellipsis [&_a_.grow]:whitespace-nowrap [&_a_>_span:not(.icon):not(.badge)]:overflow-hidden [&_a_>_span:not(.icon):not(.badge)]:text-ellipsis [&_a_>_span:not(.icon):not(.badge)]:whitespace-nowrap [&_a_.badge]:shrink-0 [&_a_.badge]:max-w-[45%] [&_a_.badge]:overflow-hidden [&_a_.badge]:text-ellipsis [&_.nav-title]:flex [&_.nav-title]:items-center [&_.nav-title]:justify-between [&_.nav-title]:w-full [&_.nav-title]:p-[9px_10px_3px] [&_.nav-title]:border-0 [&_.nav-title]:bg-none [&_.nav-title]:font-inherit [&_.nav-title]:text-[8px] [&_.nav-title]:font-medium [&_.nav-title]:normal-case [&_.nav-title]:tracking-[0.04em] [&_.nav-title]:text-faint [&_.nav-title]:cursor-default [&_.nav-title:hover]:text-faint [&_.nav-title_.icon]:transition-[transform_0.15s] [&_a]:relative [&_a]:h-8 [&_a_.icon]:w-[17px] [&_a_.icon]:h-[17px] [&_a.active]:shadow-none [&_a.active::before]:hidden [&_a_.icon]:text-inherit [&_a.active_.icon]:text-brand">
            {env ? (
              ENV_NAV.map((group) => (
                <div key={group.title || "top"} role="group" aria-label={group.title || "Overview"} className="nav-group [&_+_.nav-group]:mt-1.5 [&.closed_.nav-title_.icon]:transform-[rotate(-90deg)] [&.closed_.nav-items]:hidden">
                  {group.title && <div className="nav-title">{group.title}</div>}
                  <div className="nav-items">
                    {group.items.map(([key, label, to, icon]) => (
                      <Link key={key} data-tour={`nav-${key}`} href={envHref(env, to || key)} className={section === key || (key === "activity" && ACTIVITY_SECTIONS.includes(section)) || (key === "data" && DATA_SECTIONS.includes(section)) || (key === "webhooks" && WEBHOOK_PAGES.some(([page]) => page === section)) ? "active" : ""} title={label}>
                        <Icon name={icon || key} size={16} /><span className="label whitespace-nowrap">{label}</span>
                      </Link>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <>
                <div className="nav-title text-2xs font-semibold uppercase tracking-[0.08em] text-faint p-[16px_10px_6px] [&::after]:hidden">{name}</div>
                {(envs || []).map((e, i) => (
                  <Link key={e.name} href={envHref(e.name, "overview")}>
                    <span className={`avatar grid place-items-center font-extrabold text-base text-pastel-ink bg-lavender shrink-0 [&.pastel]:bg-sunken [&.pastel]:text-text [&.pastel]:[border:1px_solid_var(--line-2)] [&.pastel.lavender]:bg-sunken [&.pastel.lavender]:text-text [&.pastel.lavender]:[border:1px_solid_var(--line-2)] pastel text-text pastel-contrast [&.lavender]:bg-lavender [&.peach]:bg-peach [&.mint]:bg-mint [&.butter]:bg-butter [&.sky]:bg-sky [&.rose]:bg-rose [&.lavender]:text-text [&.peach]:text-text [&.mint]:text-text [&.butter]:text-text [&.sky]:text-text [&.rose]:text-text ${envTone(e.name, i)}`} style={{ width: 18, height: 18, fontSize: 10 }}>{e.name.slice(0, 1).toUpperCase()}</span>
                    <span className="label whitespace-nowrap">{e.name}</span>
                  </Link>
                ))}
              </>
            )}
          </nav>
        </aside>
        </div>
        <div className={`workspace flex-1 min-w-0 min-h-0 flex flex-col ${subnav ? "has-secondary" : ""}`}>
          {subnav && <nav className="secondary-bar h-[var(--bar)] [flex:0_0_var(--bar)] flex items-center gap-2.5 p-[0_16px] [border-bottom:1px_solid_var(--line)] bg-[color-mix(in_srgb,_var(--canvas)_94%,_var(--surface))] text-base [&_b]:font-semibold [&_.subnav-action]:ml-auto" aria-label={`${subnav.title} section navigation`}>
            <b>{subnav.title}</b>
            {subnav.action && <button type="button" className="subnav-action grid place-items-center w-control-sm h-control-sm [border:1px_solid_var(--line-2)] rounded-sm bg-transparent text-text cursor-pointer [&:hover]:bg-hover" onClick={subnav.action.onClick} title={subnav.action.label} aria-label={subnav.action.label}><Icon name="plus" size={16} /></button>}
          </nav>}
          <div className="workspace-body flex-1 min-h-0 flex">
            {subnav && <SubNav subnav={subnav} backHref={env ? envHref(env, "overview") : "/"} showHeader={false} />}
            {navOpen && <div className="sheet-overlay fixed inset-0 bg-[rgba(6,_5,_10,_0.55)] backdrop-filter-none z-[50] animate-[fade_0.15s_ease]" style={{ zIndex: 39 }} onClick={() => setNavOpen(false)} />}
            <div className="main-col flex-1 min-w-0 flex flex-col h-full [&_.main]:flex-1 [&_.main]:min-h-0">
              <main className={`main flex-1 min-w-0 overflow-auto bg-canvas rounded-none [&_>_.topbar]:hidden ${flowPage ? "flow-main" : ""}`}>
                <div className={`content p-[4px_32px_64px] max-w-[1320px] [&.full]:max-w-none [&.full]:p-0 [&.full]:h-[calc(100%_-_76px)] max-navigation:p-[4px_16px_48px] [&_>_:where(.card,_.form-section,_section,_.alert,_.notice)_+_:where(.card,_.form-section,_section,_.alert,_.notice)]:mt-4 ${full ? "full" : ""} ${flowPage ? "flow-content" : ""}`}>
                  {children}
                </div>
              </main>
              <Console env={env} />
            </div>
          </div>
        </div>
       </div>
        {newEnv && <NewEnvironment envs={envs || []} onClose={() => setNewEnv(false)} onDone={(created) => router.visit(envHref(created, "overview"))} />}
        <Tour env={env} section={section} />
        {historyOpen && env && <BranchHistory runtime={runtime || { name }} env={env} url={url} onClose={closeHistory} />}
      </div>
    </ToastProvider>
  );
}
