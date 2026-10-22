import { Link, router } from "@inertiajs/react";
import { useMemo, useState } from "react";
import CommandSearch from "./CommandSearch";
import StatusLights from "./StatusLights";
import { Icon } from "./icons";
import { LogoMark } from "./Logo";
import { Popover } from "./ui";
import { envPath, useApi } from "../lib/api";
import { readTour, startTour } from "../lib/tour";

function envHref(env, section = "overview") {
  return section === "overview" ? `/envs/${env}` : `/envs/${env}/${section}`;
}

function initial(text) {
  return (text || "?").trim().slice(0, 1).toUpperCase();
}

/** Which project an environment belongs to, and the project's other environments. */
function useProjects(runtime, envs, env) {
  return useMemo(() => {
    const list = runtime?.projects || [];
    const current = (envs || []).find((e) => e.name === env);
    const project = list.find((p) => p.id === current?.project_id) || null;
    const siblings = project ? (envs || []).filter((e) => e.project_id === project.id) : envs || [];
    const counts = Object.fromEntries(list.map((p) => [p.id, (envs || []).filter((e) => e.project_id === p.id).length]));
    return { projects: list, project, siblings, counts, current };
  }, [runtime, envs, env]);
}

function SearchField({ value, onChange, placeholder }) {
  return (
    <div className="pop-search flex items-center gap-2 m-[2px_8px_6px] p-[0_10px] h-control [border:1px_solid_var(--line-2)] rounded-sm text-faint [&:focus-within]:border-brand [&_input]:flex-1 [&_input]:min-w-0 [&_input]:h-full [&_input]:p-0 [&_input]:border-0 [&_input]:bg-transparent [&_input]:shadow-none! [&_input]:text-ui">
      <Icon name="search" size={14} />
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
    </div>
  );
}

function ProjectMenu({ runtime, envs, env, section, confined }) {
  const { projects, project, counts } = useProjects(runtime, envs, env);
  const [query, setQuery] = useState("");
  const name = project?.name || runtime?.name || "Pawabase";
  const shown = projects.filter((p) => `${p.name} ${p.slug}`.toLowerCase().includes(query.toLowerCase().trim()));
  const open = (p) => {
    const first = (envs || []).find((e) => e.project_id === p.id && e.is_default) || (envs || []).find((e) => e.project_id === p.id);
    if (first) router.visit(envHref(first.name, section || "overview"));
  };
  return (
    <Popover
      label="Projects"
      width={340}
      trigger={(props) => (
        <button type="button" className="ctx-btn inline-flex items-center gap-2 h-control max-w-[280px] p-[0_8px_0_6px] [border:1px_solid_transparent] rounded-sm bg-none text-text font-inherit text-ui font-medium-plus cursor-pointer [&:hover]:bg-hover [&[aria-expanded=true]]:bg-hover [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.env]:font-medium max-navigation:[&.env_.ctx-tag]:hidden min-w-0" data-tour="env-menu" title="Switch project" {...props}>
          <span className="ctx-avatar grid place-items-center w-[22px] h-[22px] shrink-0 [border:1px_solid_var(--line-2)] rounded-xs bg-sunken text-text text-2xs font-bold">{initial(name)}</span>
          <span className="ctx-name overflow-hidden text-ellipsis whitespace-nowrap">{name}</span>
          {project?.database?.status && <span className={`ctx-dot w-[7px] h-[7px] rounded-full shrink-0 bg-[var(--faint)] [&.ok]:bg-ok [&.warn]:bg-warn ${project.database.status === "ready" ? "ok" : "warn"}`} title={`Database ${project.database.status}`} />}
          <Icon name="chevronsUpDown" size={14} className="faint text-faint" />
        </button>
      )}
    >
      {({ close }) => (
        <>
          <div className="pop-head flex items-center justify-between gap-2 p-[10px_12px_6px] [&_.eyebrow]:text-2xs [&_.eyebrow]:tracking-[0.06em]">
            <span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Projects</span>
            <span className="faint text-faint small text-ui-sm">{projects.length || 1}</span>
          </div>
          {projects.length > 6 && <SearchField value={query} onChange={setQuery} placeholder="Find a project…" />}
          <div className="pop-list flex flex-col gap-px p-[2px_6px_6px] overflow-y-auto min-h-0" role="listbox" aria-label="Projects">
            {projects.length === 0 && (
              <div className="pop-item flex items-center gap-2.5 w-full min-h-10 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:outline-none [&.current]:bg-press [&:disabled]:opacity-50 [&:disabled]:cursor-default [&_b]:block [&_b]:font-medium-plus [&_b]:text-ui [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap [&_b]:normal-case [&.finding_b]:normal-case [&_small]:block [&_small]:text-muted [&_small]:text-xs [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap [&.finding_small]:whitespace-normal [&_.grow]:min-w-0 [&_.ctx-avatar]:w-[28px] [&_.ctx-avatar]:h-[28px] [&_.ctx-avatar]:text-xs [&.finding_small]:normal-case current" role="option" aria-selected="true">
                <span className="ctx-avatar grid place-items-center w-[22px] h-[22px] shrink-0 [border:1px_solid_var(--line-2)] rounded-xs bg-sunken text-text text-2xs font-bold">{initial(name)}</span>
                <span className="grow flex-1 min-w-0"><b>{name}</b><small>This Studio manages one project</small></span>
                <Icon name="check" size={14} />
              </div>
            )}
            {shown.map((p) => (
              <button key={p.id} type="button" role="option" aria-selected={p.id === project?.id} className={`pop-item flex items-center gap-2.5 w-full min-h-10 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:outline-none [&.current]:bg-press [&:disabled]:opacity-50 [&:disabled]:cursor-default [&_b]:block [&_b]:font-medium-plus [&_b]:text-ui [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap [&_b]:normal-case [&.finding_b]:normal-case [&_small]:block [&_small]:text-muted [&_small]:text-xs [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap [&.finding_small]:whitespace-normal [&_.grow]:min-w-0 [&_.ctx-avatar]:w-[28px] [&_.ctx-avatar]:h-[28px] [&_.ctx-avatar]:text-xs [&.finding_small]:normal-case ${p.id === project?.id ? "current" : ""}`} onClick={() => { close(false); open(p); }} disabled={!counts[p.id]}>
                <span className="ctx-avatar grid place-items-center w-[22px] h-[22px] shrink-0 [border:1px_solid_var(--line-2)] rounded-xs bg-sunken text-text text-2xs font-bold">{initial(p.name)}</span>
                <span className="grow flex-1 min-w-0"><b>{p.name}</b><small>{p.slug} · {counts[p.id] || 0} environment{counts[p.id] === 1 ? "" : "s"}</small></span>
                {p.id === project?.id ? <Icon name="check" size={14} /> : <span className={`ctx-dot w-[7px] h-[7px] rounded-full shrink-0 bg-[var(--faint)] [&.ok]:bg-ok [&.warn]:bg-warn ${p.database?.status === "ready" ? "ok" : "warn"}`} title={`Database ${p.database?.status || "not bound"}`} />}
              </button>
            ))}
            {projects.length > 0 && shown.length === 0 && <p className="pop-empty m-0 p-[14px_12px] text-muted text-ui-sm">No project matches “{query}”.</p>}
          </div>
          {!confined && (
            <div className="pop-foot flex flex-col gap-px p-1.5 [border-top:1px_solid_var(--line)]">
              <Link href="/environments" className="pop-action flex items-center gap-2.5 h-8 p-[0_8px] border-0 rounded-xs bg-none text-muted font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:text-text [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:text-text [&:focus-visible]:outline-none" role="menuitem" onClick={() => close(false)}><Icon name="projects" size={15} />All environments</Link>
              <Link href="/audit" className="pop-action flex items-center gap-2.5 h-8 p-[0_8px] border-0 rounded-xs bg-none text-muted font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:text-text [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:text-text [&:focus-visible]:outline-none" role="menuitem" onClick={() => close(false)}><Icon name="audit" size={15} />Audit log</Link>
            </div>
          )}
        </>
      )}
    </Popover>
  );
}

function EnvSwitcher({ runtime, envs, env, section, onCreate, onHistory }) {
  const { siblings, current } = useProjects(runtime, envs, env);
  const [query, setQuery] = useState("");
  const shown = siblings.filter((e) => (e.slug || e.name).toLowerCase().includes(query.toLowerCase().trim()));
  if (!env) return null;
  const label = current?.slug || env;
  return (
    <Popover
      label="Environments"
      width={320}
      trigger={(props) => (
        <button type="button" className="ctx-btn inline-flex items-center gap-2 h-control max-w-[280px] p-[0_8px_0_6px] [border:1px_solid_transparent] rounded-sm bg-none text-text font-inherit text-ui font-medium-plus cursor-pointer [&:hover]:bg-hover [&[aria-expanded=true]]:bg-hover [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.env]:font-medium max-navigation:[&.env_.ctx-tag]:hidden min-w-0 env" title="Switch environment" {...props}>
          <Icon name="layers" size={14} className="faint text-faint" />
          <span className="ctx-name overflow-hidden text-ellipsis whitespace-nowrap">{label}</span>
          {current?.is_default && <span className="ctx-tag p-[0_6px] h-[18px] inline-grid place-items-center [border:1px_solid_var(--line-2)] rounded-xs text-muted text-2xs font-medium-plus [&.warn]:text-warn [&.warn]:[border-color:color-mix(in_srgb,_var(--warn)_40%,_transparent)]">default</span>}
          {current?.preview_source && <span className="ctx-tag p-[0_6px] h-[18px] inline-grid place-items-center [border:1px_solid_var(--line-2)] rounded-xs text-muted text-2xs font-medium-plus [&.warn]:text-warn [&.warn]:[border-color:color-mix(in_srgb,_var(--warn)_40%,_transparent)] warn">preview</span>}
          <Icon name="chevronsUpDown" size={14} className="faint text-faint" />
        </button>
      )}
    >
      {({ close }) => (
        <>
          <div className="pop-head flex items-center justify-between gap-2 p-[10px_12px_6px] [&_.eyebrow]:text-2xs [&_.eyebrow]:tracking-[0.06em]">
            <span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Environments</span>
            <span className="faint text-faint small text-ui-sm">{siblings.length}</span>
          </div>
          {siblings.length > 6 && <SearchField value={query} onChange={setQuery} placeholder="Find an environment…" />}
          <div className="pop-list flex flex-col gap-px p-[2px_6px_6px] overflow-y-auto min-h-0" role="listbox" aria-label="Environments">
            {shown.map((e) => (
              <Link key={e.name} href={envHref(e.name, section || "overview")} role="option" aria-selected={e.name === env} className={`pop-item flex items-center gap-2.5 w-full min-h-10 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:outline-none [&.current]:bg-press [&:disabled]:opacity-50 [&:disabled]:cursor-default [&_b]:block [&_b]:font-medium-plus [&_b]:text-ui [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap [&_b]:normal-case [&.finding_b]:normal-case [&_small]:block [&_small]:text-muted [&_small]:text-xs [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap [&.finding_small]:whitespace-normal [&_.grow]:min-w-0 [&_.ctx-avatar]:w-[28px] [&_.ctx-avatar]:h-[28px] [&_.ctx-avatar]:text-xs [&.finding_small]:normal-case ${e.name === env ? "current" : ""}`} onClick={() => close(false)}>
                <Icon name="layers" size={15} className="faint text-faint" />
                <span className="grow flex-1 min-w-0"><b>{e.slug || e.name}</b><small>{e.preview_source ? `Preview of ${e.preview_source}` : e.version ? `Definitions v${e.version}` : "No definitions yet"}</small></span>
                {e.is_default && <span className="ctx-tag p-[0_6px] h-[18px] inline-grid place-items-center [border:1px_solid_var(--line-2)] rounded-xs text-muted text-2xs font-medium-plus [&.warn]:text-warn [&.warn]:[border-color:color-mix(in_srgb,_var(--warn)_40%,_transparent)]">default</span>}
                {e.name === env && <Icon name="check" size={14} />}
              </Link>
            ))}
            {shown.length === 0 && <p className="pop-empty m-0 p-[14px_12px] text-muted text-ui-sm">No environment matches “{query}”.</p>}
          </div>
          <div className="pop-foot flex flex-col gap-px p-1.5 [border-top:1px_solid_var(--line)]">
            <button type="button" className="pop-action flex items-center gap-2.5 h-8 p-[0_8px] border-0 rounded-xs bg-none text-muted font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:text-text [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:text-text [&:focus-visible]:outline-none" role="menuitem" onClick={() => { close(false); onCreate(); }}><Icon name="plus" size={15} />Create environment</button>
            <button type="button" className="pop-action flex items-center gap-2.5 h-8 p-[0_8px] border-0 rounded-xs bg-none text-muted font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:text-text [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:text-text [&:focus-visible]:outline-none" role="menuitem" onClick={() => { close(false); onHistory(); }}><Icon name="gitBranch" size={15} />Branches and history</button>
            <Link href={envHref(env, "settings")} className="pop-action flex items-center gap-2.5 h-8 p-[0_8px] border-0 rounded-xs bg-none text-muted font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:text-text [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:text-text [&:focus-visible]:outline-none" role="menuitem" onClick={() => close(false)}><Icon name="settings" size={15} />Environment settings</Link>
          </div>
        </>
      )}
    </Popover>
  );
}

const CREATE = [
  ["tables", "Table", "A table exposed as a REST API", "resources"],
  ["routes", "Route", "A custom endpoint for a flow or function", "routes"],
  ["policies", "Policy", "A reusable access rule", "policies"],
  ["flows", "Flow", "A visual workflow", "flows", "/flows/new"],
  ["storage", "Bucket", "Where uploaded files live", "storage"],
  ["webhooks", "Webhook", "Send events to another service", "webhooks"],
  ["schedules", "Schedule", "Run something on a timer", "schedules"],
];

function NewMenu({ env }) {
  if (!env) return null;
  return (
    <Popover
      label="Create"
      width={300}
      align="end"
          trigger={(props) => <button type="button" className="btn primary appbar-btn inline-flex items-center gap-1.5 h-control p-[0_12px] font-inherit text-ui font-medium-plus cursor-pointer whitespace-nowrap [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] max-navigation:hidden" title="Create something" {...props}><Icon name="plus" size={14} /><span className="new-label">New</span></button>}
    >
      {({ close }) => (
        <>
          <div className="pop-head flex items-center justify-between gap-2 p-[10px_12px_6px] [&_.eyebrow]:text-2xs [&_.eyebrow]:tracking-[0.06em]"><span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Create in {env}</span></div>
          <div className="pop-list flex flex-col gap-px p-[2px_6px_6px] overflow-y-auto min-h-0">
            {CREATE.map(([key, label, hint, icon, path]) => (
              <Link key={key} role="menuitem" className="pop-item flex items-center gap-2.5 w-full min-h-10 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:outline-none [&.current]:bg-press [&:disabled]:opacity-50 [&:disabled]:cursor-default [&_b]:block [&_b]:font-medium-plus [&_b]:text-ui [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap [&_b]:normal-case [&.finding_b]:normal-case [&_small]:block [&_small]:text-muted [&_small]:text-xs [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap [&.finding_small]:whitespace-normal [&_.grow]:min-w-0 [&_.ctx-avatar]:w-[28px] [&_.ctx-avatar]:h-[28px] [&_.ctx-avatar]:text-xs [&.finding_small]:normal-case" href={path ? `/envs/${env}${path}` : `${envHref(env, key)}?new=1`} onClick={() => close(false)}>
                <Icon name={icon} size={15} className="faint text-faint" />
                <span className="grow flex-1 min-w-0"><b>{label}</b><small>{hint}</small></span>
              </Link>
            ))}
          </div>
        </>
      )}
    </Popover>
  );
}

/** What the platform says needs attention right now: the same findings the Activity overview shows. */
function Advisor({ env }) {
  const system = useApi(env ? `${envPath(env)}/advisor` : null, { interval: 60000 });
  if (!env) return null;
  const findings = system.data?.findings || [];
  const critical = findings.filter((f) => f.severity === "critical").length;
  return (
    <Popover
      label="Advisor"
      width={380}
      align="end"
      trigger={(props) => (
        <button type="button" className="appbar-icon grid place-items-center w-control h-control border-0 rounded-[calc(var(--r)_-_1px)] bg-none text-muted cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] relative advisor" title={findings.length ? `${findings.length} finding${findings.length === 1 ? "" : "s"} need attention` : "Advisor: nothing needs attention"} aria-label="Advisor" {...props}>
          <Icon name="bell" size={16} />
          {findings.length > 0 && <span className={`appbar-count absolute top-[3px] right-0.5 min-w-[15px] h-[15px] p-[0_4px] grid place-items-center rounded-[99px] bg-warn text-[#1a1405] text-[9.5px] font-extrabold leading-[1] [&.critical]:bg-danger [&.critical]:text-[#1f0b0a] ${critical ? "critical" : ""}`}>{findings.length}</span>}
        </button>
      )}
    >
      {({ close }) => (
        <>
          <div className="pop-head flex items-center justify-between gap-2 p-[10px_12px_6px] [&_.eyebrow]:text-2xs [&_.eyebrow]:tracking-[0.06em]">
            <span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Advisor</span>
            <span className="faint text-faint small text-ui-sm">last hour</span>
          </div>
          {system.loading && !system.data ? <p className="pop-empty m-0 p-[14px_12px] text-muted text-ui-sm">Checking…</p> : findings.length === 0 ? (
            <div className="pop-clear flex gap-2.5 items-start p-[8px_12px_14px] text-ok [&_b]:block [&_b]:text-text [&_b]:font-semibold [&_b]:text-ui [&_small]:block [&_small]:text-muted [&_small]:text-xs"><Icon name="check" size={16} /><span><b>All clear</b><small>Requests, functions, jobs, flows, webhooks and mail are healthy, and nothing is close to a limit.</small></span></div>
          ) : (
            <div className="pop-list flex flex-col gap-px p-[2px_6px_6px] overflow-y-auto min-h-0">
              {findings.map((f, i) => (
                <Link key={i} role="menuitem" className="pop-item flex items-center gap-2.5 w-full min-h-10 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:outline-none [&.current]:bg-press [&:disabled]:opacity-50 [&:disabled]:cursor-default [&_b]:block [&_b]:font-medium-plus [&_b]:text-ui [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap [&_b]:normal-case [&.finding_b]:normal-case [&_small]:block [&_small]:text-muted [&_small]:text-xs [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap [&.finding_small]:whitespace-normal [&_.grow]:min-w-0 [&_.ctx-avatar]:w-[28px] [&_.ctx-avatar]:h-[28px] [&_.ctx-avatar]:text-xs [&.finding_small]:normal-case finding flex gap-2.5 items-start w-full text-left border-0 bg-none font-inherit text-inherit p-[9px_6px] rounded-sm cursor-pointer [&:hover]:bg-sunken [&_.dot]:mt-1.5 [&_small]:block [&_small]:text-muted [&_small]:text-caption [&_small]:capitalize" href={envHref(env, f.page || "observability")} onClick={() => close(false)}>
                  <span className={`sev w-2 h-2 rounded-full shrink-0 bg-warn [&.critical]:bg-danger ${f.severity}`} />
                  <span className="grow flex-1 min-w-0"><b>{f.title}</b><small>{f.message}</small></span>
                </Link>
              ))}
            </div>
          )}
          <div className="pop-foot flex flex-col gap-px p-1.5 [border-top:1px_solid_var(--line)]"><Link href={envHref(env, "activity")} className="pop-action flex items-center gap-2.5 h-8 p-[0_8px] border-0 rounded-xs bg-none text-muted font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:text-text [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:text-text [&:focus-visible]:outline-none" role="menuitem" onClick={() => close(false)}><Icon name="observability" size={15} />Open activity</Link></div>
        </>
      )}
    </Popover>
  );
}

function HelpMenu({ env, confined }) {
  const seen = readTour();
  const resume = seen.status === "paused" && seen.step > 0;
  return (
    <Popover
      label="Help"
      width={300}
      align="end"
      trigger={(props) => <button type="button" className="appbar-icon grid place-items-center w-control h-control border-0 rounded-[calc(var(--r)_-_1px)] bg-none text-muted cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] relative" title="Help" aria-label="Help" {...props}><Icon name="help" size={16} /></button>}
    >
      {({ close }) => (
        <>
          <div className="pop-list flex flex-col gap-px p-[2px_6px_6px] overflow-y-auto min-h-0">
            {env && <button type="button" role="menuitem" className="pop-item flex items-center gap-2.5 w-full min-h-10 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:outline-none [&.current]:bg-press [&:disabled]:opacity-50 [&:disabled]:cursor-default [&_b]:block [&_b]:font-medium-plus [&_b]:text-ui [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap [&_b]:normal-case [&.finding_b]:normal-case [&_small]:block [&_small]:text-muted [&_small]:text-xs [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap [&.finding_small]:whitespace-normal [&_.grow]:min-w-0 [&_.ctx-avatar]:w-[28px] [&_.ctx-avatar]:h-[28px] [&_.ctx-avatar]:text-xs [&.finding_small]:normal-case" onClick={() => { close(false); startTour(resume ? { step: seen.step } : {}); }}><Icon name="sparkle" size={15} className="faint text-faint" /><span className="grow flex-1 min-w-0"><b>{resume ? "Resume the tour" : "Take the tour"}</b><small>{resume ? `Pick up at step ${seen.step + 1}` : "A guided walk through Studio"}</small></span></button>}
            {env && <a role="menuitem" className="pop-item flex items-center gap-2.5 w-full min-h-10 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:outline-none [&.current]:bg-press [&:disabled]:opacity-50 [&:disabled]:cursor-default [&_b]:block [&_b]:font-medium-plus [&_b]:text-ui [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap [&_b]:normal-case [&.finding_b]:normal-case [&_small]:block [&_small]:text-muted [&_small]:text-xs [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap [&.finding_small]:whitespace-normal [&_.grow]:min-w-0 [&_.ctx-avatar]:w-[28px] [&_.ctx-avatar]:h-[28px] [&_.ctx-avatar]:text-xs [&.finding_small]:normal-case" href={`/envs/${env}/api-docs`} target="_blank" rel="noreferrer" onClick={() => close(false)}><Icon name="book" size={15} className="faint text-faint" /><span className="grow flex-1 min-w-0"><b>API reference</b><small>This environment's live OpenAPI docs</small></span><Icon name="external" size={13} className="faint text-faint" /></a>}
            {!confined && <Link role="menuitem" className="pop-item flex items-center gap-2.5 w-full min-h-10 p-[6px_8px] border-0 rounded-xs bg-none text-text font-inherit text-ui text-left cursor-pointer [&:hover]:bg-hover [&:hover]:outline-none [&:focus-visible]:bg-hover [&:focus-visible]:outline-none [&.current]:bg-press [&:disabled]:opacity-50 [&:disabled]:cursor-default [&_b]:block [&_b]:font-medium-plus [&_b]:text-ui [&_b]:overflow-hidden [&_b]:text-ellipsis [&_b]:whitespace-nowrap [&_b]:normal-case [&.finding_b]:normal-case [&_small]:block [&_small]:text-muted [&_small]:text-xs [&_small]:overflow-hidden [&_small]:text-ellipsis [&_small]:whitespace-nowrap [&.finding_small]:whitespace-normal [&_.grow]:min-w-0 [&_.ctx-avatar]:w-[28px] [&_.ctx-avatar]:h-[28px] [&_.ctx-avatar]:text-xs [&.finding_small]:normal-case" href="/audit" onClick={() => close(false)}><Icon name="audit" size={15} className="faint text-faint" /><span className="grow flex-1 min-w-0"><b>Audit log</b><small>Who changed what</small></span></Link>}
          </div>
          <div className="pop-head flex items-center justify-between gap-2 p-[10px_12px_6px] [&_.eyebrow]:text-2xs [&_.eyebrow]:tracking-[0.06em]"><span className="eyebrow text-2xs font-bold uppercase tracking-[0.09em] text-faint">Keyboard</span></div>
          <dl className="pop-keys grid grid-cols-[1fr_auto] gap-[6px_12px] m-0 p-[4px_12px_12px] text-ui-sm [&_dt]:text-muted [&_dd]:m-0 [&_dd]:flex [&_dd]:gap-[3px]">
            <dt>Search Studio</dt><dd><kbd>⌘</kbd><kbd>K</kbd></dd>
            <dt>Terminal</dt><dd><kbd>⌘</kbd><kbd>J</kbd></dd>
            <dt>Find in terminal</dt><dd><kbd>⌘</kbd><kbd>F</kbd></dd>
            <dt>Close a panel</dt><dd><kbd>Esc</kbd></dd>
          </dl>
        </>
      )}
    </Popover>
  );
}

export default function TopBar({ runtime, envs, env, section, confined, crumbs = [], onMenu, onHistory, onCreateEnv }) {
  return (
    <header className="appbar flex items-center gap-1.5 h-[var(--bar)] p-[0_12px_0_0] [border-bottom:1px_solid_var(--line)] bg-[color-mix(in_srgb,_var(--canvas)_94%,_var(--surface))] relative z-[35] min-w-0 [&_.menu-btn]:hidden [&_.lights]:h-6 [&_.lights]:p-[0_8px_0_12px] [&_.lights]:[border-left:1px_solid_var(--line)] [&_.lights]:rounded-none [&_.lights]:ml-1 [&_.lights:hover]:bg-none [&_.top-search_.cmd-field]:h-control [&_.top-search_.cmd-field]:w-[240px] [&_.top-search_.cmd-field]:p-[0_6px_0_10px] [&_.top-search_.cmd-field]:[border:1px_solid_var(--line-2)] [&_.top-search_.cmd-field]:rounded-sm [&_.top-search_.cmd-field]:bg-transparent [&_.top-search_.cmd-field]:shadow-none [&_.cmd.open_.cmd-field]:w-[340px] [&_.cmd.open_.cmd-field]:border-brand [&_.cmd.open_.cmd-field]:shadow-none [&_.cmd-field:focus-within]:w-[340px] [&_.cmd-field:focus-within]:border-brand [&_.cmd-field:focus-within]:shadow-none max-navigation:[&_.menu-btn]:grid max-navigation:[&_.menu-btn]:ml-2 max-navigation:[&_.top-search_.cmd-field]:w-[160px] max-navigation:[&_.cmd.open_.cmd-field]:w-[160px] max-wide:[&_.top-search_.cmd-field]:w-[170px] w-full pl-4 max-navigation:pl-2 overflow-visible [&_.top-search]:min-w-0 [&_.top-search]:shrink-0 [&_.top-search_.cmd-field]:max-w-[100%] [&_.popover-wrap]:shrink-0 [&_.lights]:shrink-0 max-wide:[&_.cmd.open_.cmd-field]:w-[170px] max-search:[&_.new-label]:hidden max-search:[&_.top-search_.cmd-field_kbd]:hidden max-search:[&_.top-search_.cmd-field]:w-[130px] max-search:[&_.cmd.open_.cmd-field]:w-[130px]">
      <button type="button" className="appbar-icon grid place-items-center w-control h-control border-0 rounded-[calc(var(--r)_-_1px)] bg-none text-muted cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] relative menu-btn hidden max-navigation:grid" onClick={onMenu} aria-label="Open navigation"><Icon name="menu" size={16} /></button>
      <Link href="/" className="appbar-brand flex place-items-center w-auto h-auto shrink-0 border-r-0 max-navigation:w-auto! max-navigation:p-[0_8px]! max-navigation:border-r-0 gap-[9px] items-center justify-start flex-row p-[0_6px_0_0]" title="Pawabase Studio">
        <LogoMark size={24} />
      </Link>
      <nav className="appbar-context flex items-center gap-0.5 min-w-0 pl-2.5 ml-1 [border-left:1px_solid_var(--line)] h-[28px] shrink [flex:0_1_auto] overflow-visible [&_.popover-wrap]:shrink [&_.popover-wrap]:min-w-0 [&_.ctx-btn]:max-w-[100%]" aria-label="Location">
        <ProjectMenu runtime={runtime} envs={envs} env={env} section={section} confined={confined} />
        {env && <span className="ctx-sep text-line-2 text-[18px] font-[300] p-[0_2px]">/</span>}
        <EnvSwitcher runtime={runtime} envs={envs} env={env} section={section} onCreate={onCreateEnv} onHistory={onHistory} />
        {crumbs.map((c, i) => <span key={i} className="appbar-crumb inline-flex items-center gap-1.5 text-text text-ui font-medium-plus whitespace-nowrap [&_.sep]:text-faint"><span className="ctx-sep text-line-2 text-[18px] font-[300] p-[0_2px]">/</span>{c}</span>)}
      </nav>
      <span className="appbar-spacer flex-1" />
      <span data-tour="search" className="tour-wrap inline-flex top-search"><CommandSearch /></span>
      <NewMenu env={env} />
      <div className="appbar-icons flex items-center gap-0.5 pl-1.5 ml-1 [border-left:1px_solid_var(--line)] h-[28px] shrink-0">
        {env && <button type="button" className="appbar-icon grid place-items-center w-control h-control border-0 rounded-[calc(var(--r)_-_1px)] bg-none text-muted cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] relative" data-tour="history" onClick={onHistory} title="Branches and history" aria-label="Branches and history"><Icon name="gitBranch" size={16} /></button>}
        {env && <button type="button" className="appbar-icon grid place-items-center w-control h-control border-0 rounded-[calc(var(--r)_-_1px)] bg-none text-muted cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] relative" onClick={() => window.dispatchEvent(new CustomEvent("pawabase:console"))} title="Terminal (⌘J)" aria-label="Toggle the terminal"><Icon name="terminal" size={16} /></button>}
        <Advisor env={env} />
        <HelpMenu env={env} confined={confined} />
      </div>
      <StatusLights />
    </header>
  );
}
