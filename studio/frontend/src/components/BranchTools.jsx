// Branches in Studio: see which one you are on, switch, make one, merge it, throw it away.
//
// A branch is a safe copy of an environment's setup (tables, rules, flows, email). Changes made on a branch stay on it until
// it is merged into `main`, which is the live version. `main` always exists, so there is always somewhere to go back to.

import { router } from "@inertiajs/react";
import { useEffect, useState } from "react";
import { Icon } from "./icons";
import { Badge, Button, Field, Modal, Popover, useAction, useToast } from "./ui";
import { del, envPath, post, useApi } from "../lib/api";

const storageKey = (env) => `pawabase.branch.${env}`;

/** The branch Studio is working on for *env*. Every request carries it (see lib/api.js), so this is what you are looking at. */
export function readBranch(env) {
  try {
    return localStorage.getItem(storageKey(env)) || new URLSearchParams(window.location.search).get("branch") || "main";
  } catch {
    return "main";
  }
}

export function useBranch(env) {
  const [branch, setBranch] = useState(() => readBranch(env));
  useEffect(() => {
    setBranch(readBranch(env));
    const sync = (event) => { if (!event.detail || event.detail.env === env) setBranch(readBranch(env)); };
    window.addEventListener("pawabase:branch", sync);
    return () => window.removeEventListener("pawabase:branch", sync);
  }, [env]);
  return branch;
}

/** Make *name* the working branch and reload the page on it. `keepSheet` reopens the History panel afterwards. */
export function switchBranch(env, name, { keepSheet = false } = {}) {
  try { localStorage.setItem(storageKey(env), name); } catch { /* the address still carries it */ }
  window.dispatchEvent(new CustomEvent("pawabase:branch", { detail: { env, branch: name } }));
  const target = new URL(window.location.href);
  if (name === "main") target.searchParams.delete("branch");
  else target.searchParams.set("branch", name);
  if (keepSheet) target.searchParams.set("history", "1");
  else target.searchParams.delete("history");
  router.visit(`${target.pathname}${target.search}`, { preserveScroll: true, preserveState: false });
}

const edits = (branch) => (branch.changes || []).filter((c) => c.action !== "merged").length;
const wasMerged = (branch) => (branch.changes || []).at(-1)?.action === "merged";
const NAME = /^[a-z0-9][a-z0-9._-]{0,62}$/;

function useBranches(env) {
  const branches = useApi(envPath(env, "/branches"));
  return { ...branches, list: branches.data?.data || [{ name: "main", protected: true }] };
}

/** The top bar's branch button: the branch you are on, and a list to switch to another. */
export function BranchSwitcher({ env, onManage }) {
  const branch = useBranch(env);
  const { list, reload } = useBranches(env);
  const [name, setName] = useState("");
  const [run, busy] = useAction();
  const create = async (close) => {
    const clean = name.trim().toLowerCase().replace(/\s+/g, "-");
    if (!NAME.test(clean)) return;
    if (await run(() => post(envPath(env, "/branches"), { name: clean, ...(branch !== "main" ? { from_branch: branch } : {}) }), `Created ${clean}`)) { close(); switchBranch(env, clean); }
  };
  const away = branch !== "main";
  return (
    <Popover
      label="Branches"
      width={300}
      trigger={(props) => (
        <button type="button" {...props} onClick={(e) => { reload(); props.onClick?.(e); }} className={`ctx-btn inline-flex items-center gap-1.5 h-control max-w-[220px] p-[0_8px] border rounded-sm font-inherit text-ui font-medium-plus cursor-pointer ${away ? "border-brand bg-brand-soft text-brand-strong" : "border-transparent bg-none text-muted [&:hover]:bg-hover [&:hover]:text-text"}`} title={away ? `You are working on the branch ${branch}` : "Branches"}>
          <Icon name="gitBranch" size={14} />
          <span className="overflow-hidden text-ellipsis whitespace-nowrap">{branch}</span>
          <Icon name="chevronsUpDown" size={12} className="faint text-faint" />
        </button>
      )}
    >
      {({ close }) => (
        <>
          <div className="pop-head flex items-center justify-between gap-2 p-[10px_12px_6px]"><span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Switch branch</span></div>
          <div className="pop-list flex flex-col gap-px p-[2px_6px_6px] overflow-y-auto" role="listbox" aria-label="Branches" style={{ maxHeight: 260 }}>
            {list.map((b) => (
              <button key={b.name} type="button" role="option" aria-selected={b.name === branch} className="pop-item flex items-center gap-2.5 w-full min-h-9 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-left text-ui cursor-pointer [&:hover]:bg-hover" onClick={() => { close(); if (b.name !== branch) switchBranch(env, b.name); }}>
                <span style={{ width: 14 }}>{b.name === branch && <Icon name="check" size={14} />}</span>
                <span className="grow flex-1 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">{b.name}</span>
                {b.name === "main" ? <span className="faint text-faint text-ui-sm">live</span> : wasMerged(b) ? <span className="faint text-faint text-ui-sm">merged</span> : edits(b) > 0 && <span className="faint text-faint text-ui-sm">{edits(b)} change{edits(b) === 1 ? "" : "s"}</span>}
              </button>
            ))}
          </div>
          <div className="pop-foot flex flex-col gap-2 p-[10px_10px_10px] [border-top:1px_solid_var(--line)]">
            <div className="row" style={{ display: "flex", gap: 6 }}>
              <input value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create(close)} placeholder="New branch, e.g. new-checkout" aria-label="New branch name" style={{ flex: 1 }} />
              <Button size="sm" variant="primary" disabled={busy || !NAME.test(name.trim().toLowerCase().replace(/\s+/g, "-"))} onClick={() => create(close)}>Create</Button>
            </div>
            <button type="button" className="linklike" style={{ alignSelf: "flex-start" }} onClick={() => { close(); onManage?.(); }}>Manage branches…</button>
          </div>
        </>
      )}
    </Popover>
  );
}

/** Shown on every page while you are off `main`, so you always know your changes are not live yet. */
export function BranchBanner({ env, onManage }) {
  const branch = useBranch(env);
  const [merging, setMerging] = useState(false);
  if (branch === "main") return null;
  return (
    <>
      <div className="branch-banner flex items-center gap-3 flex-wrap p-[8px_var(--gutter)] bg-warn-soft text-warn [border-bottom:1px_solid_color-mix(in_srgb,_var(--warn)_30%,_transparent)] text-ui" role="status">
        <Icon name="gitBranch" size={15} />
        <span className="grow flex-1 min-w-0 text-text">You are working on the branch <b>{branch}</b>. What you change here stays here until you merge it into the live version.</span>
        <Button size="sm" onClick={() => setMerging(true)}>Merge…</Button>
        <Button size="sm" onClick={() => switchBranch(env, "main")}>Back to main</Button>
      </div>
      {merging && <MergeBranch env={env} source={branch} onClose={() => setMerging(false)} />}
    </>
  );
}

/** Merge a branch into another (the live version by default), with the option to throw the branch away afterwards. */
export function MergeBranch({ env, source, onClose, onDone }) {
  const { list } = useBranches(env);
  const toast = useToast();
  const [target, setTarget] = useState("main");
  const [force, setForce] = useState(false);
  const [removeAfter, setRemoveAfter] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const diverged = error && /changed since/.test(error);
  const merge = async () => {
    setBusy(true);
    setError(null);
    try {
      await post(envPath(env, `/branches/${source}/merge`), { target, force });
      if (removeAfter) await del(envPath(env, `/branches/${source}`));
      toast(`Merged ${source} into ${target}`);
      onClose();
      if (onDone) onDone(target);
      else switchBranch(env, target === "main" || removeAfter ? target : source);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Merge ${source}`} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy} onClick={merge}>{busy ? "Merging…" : force ? "Merge and replace" : "Merge"}</Button></>}>
      <div className="stack">
        <Field label="Merge into" hint={target === "main" ? "main is the live version: it goes live as soon as you merge." : "Changes land on that branch, not on the live version."}>
          <select value={target} onChange={(e) => { setTarget(e.target.value); setError(null); setForce(false); }}>
            {list.filter((b) => b.name !== source).map((b) => <option key={b.name} value={b.name}>{b.name === "main" ? "main (live)" : b.name}</option>)}
          </select>
        </Field>
        <label className="check"><input type="checkbox" checked={removeAfter} onChange={(e) => setRemoveAfter(e.target.checked)} /> Delete the branch after merging</label>
        {error && (
          <div className="alert warn">
            {diverged ? <><b>{target} has changed since this branch was made.</b> Merging now would replace those newer changes.</> : error}
            {diverged && <label className="check" style={{ display: "flex", marginTop: 8 }}><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Merge anyway and replace them</label>}
          </div>
        )}
      </div>
    </Modal>
  );
}

/** The Branches tab of the History panel: every branch with what you can do to it. */
export function BranchesPanel({ env, branch }) {
  const { list, reload } = useBranches(env);
  const [name, setName] = useState("");
  const [merging, setMerging] = useState(null);
  const [run, busy] = useAction();
  const clean = name.trim().toLowerCase().replace(/\s+/g, "-");
  const create = async () => {
    if (await run(() => post(envPath(env, "/branches"), { name: clean, ...(branch !== "main" ? { from_branch: branch } : {}) }), `Created ${clean}`)) switchBranch(env, clean, { keepSheet: true });
  };
  const remove = async (b) => {
    if (!confirm(`Delete the branch ${b.name}? Anything on it that was not merged is lost.`)) return;
    if (await run(() => del(envPath(env, `/branches/${b.name}`)), `Deleted ${b.name}`)) {
      if (b.name === branch) switchBranch(env, "main", { keepSheet: true });
      else reload();
    }
  };
  return (
    <div className="stack lg">
      <p className="hint">A branch is a safe copy of your setup. Change things on it, then merge it into <b>main</b>, the live version, when you are happy.</p>
      <div className="list-card" style={{ padding: 0 }}>
        {list.map((b) => (
          <div key={b.name} className="row" style={{ padding: "11px 14px", borderTop: "1px solid var(--line)", gap: 10 }}>
            <Icon name="gitBranch" size={15} />
            <div className="grow" style={{ minWidth: 0 }}>
              <b>{b.name}</b>
              <div className="row wrap" style={{ gap: 6, marginTop: 3 }}>
                {b.name === branch && <Badge tone="green">You are here</Badge>}
                {b.name === "main" && <Badge>Live</Badge>}
                {wasMerged(b) && <Badge tone="blue">Merged</Badge>}
                {b.name !== "main" && edits(b) > 0 && <span className="faint text-faint text-ui-sm">{edits(b)} change{edits(b) === 1 ? "" : "s"}</span>}
              </div>
            </div>
            {b.name !== branch && <Button size="sm" disabled={busy} onClick={() => switchBranch(env, b.name, { keepSheet: true })}>Switch</Button>}
            {b.name !== "main" && <Button size="sm" disabled={busy} onClick={() => setMerging(b.name)}>Merge…</Button>}
            {b.name !== "main" && <Button size="sm" variant="danger" disabled={busy} onClick={() => remove(b)}>Delete</Button>}
          </div>
        ))}
      </div>
      <Field label="New branch" hint={`Starts as a copy of ${branch === "main" ? "the live version (main)" : branch}.`}>
        <div className="row">
          <input value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && NAME.test(clean) && create()} placeholder="new-checkout" />
          <Button variant="primary" disabled={busy || !NAME.test(clean)} onClick={create}>Create and switch</Button>
        </div>
      </Field>
      {merging && <MergeBranch env={env} source={merging} onClose={() => setMerging(null)} onDone={(target) => switchBranch(env, target === "main" ? "main" : branch, { keepSheet: true })} />}
    </div>
  );
}
