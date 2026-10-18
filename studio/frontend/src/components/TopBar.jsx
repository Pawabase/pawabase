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
    <div className="pop-search">
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
        <button type="button" className="ctx-btn" data-tour="env-menu" title="Switch project" {...props}>
          <span className="ctx-avatar">{initial(name)}</span>
          <span className="ctx-name">{name}</span>
          {project?.database?.status && <span className={`ctx-dot ${project.database.status === "ready" ? "ok" : "warn"}`} title={`Database ${project.database.status}`} />}
          <Icon name="chevronsUpDown" size={14} className="faint" />
        </button>
      )}
    >
      {({ close }) => (
        <>
          <div className="pop-head">
            <span className="eyebrow">Projects</span>
            <span className="faint small">{projects.length || 1}</span>
          </div>
          {projects.length > 6 && <SearchField value={query} onChange={setQuery} placeholder="Find a project…" />}
          <div className="pop-list" role="listbox" aria-label="Projects">
            {projects.length === 0 && (
              <div className="pop-item current" role="option" aria-selected="true">
                <span className="ctx-avatar">{initial(name)}</span>
                <span className="grow"><b>{name}</b><small>This Studio manages one project</small></span>
                <Icon name="check" size={14} />
              </div>
            )}
            {shown.map((p) => (
              <button key={p.id} type="button" role="option" aria-selected={p.id === project?.id} className={`pop-item ${p.id === project?.id ? "current" : ""}`} onClick={() => { close(false); open(p); }} disabled={!counts[p.id]}>
                <span className="ctx-avatar">{initial(p.name)}</span>
                <span className="grow"><b>{p.name}</b><small>{p.slug} · {counts[p.id] || 0} environment{counts[p.id] === 1 ? "" : "s"}</small></span>
                {p.id === project?.id ? <Icon name="check" size={14} /> : <span className={`ctx-dot ${p.database?.status === "ready" ? "ok" : "warn"}`} title={`Database ${p.database?.status || "not bound"}`} />}
              </button>
            ))}
            {projects.length > 0 && shown.length === 0 && <p className="pop-empty">No project matches “{query}”.</p>}
          </div>
          {!confined && (
            <div className="pop-foot">
              <Link href="/environments" className="pop-action" role="menuitem" onClick={() => close(false)}><Icon name="projects" size={15} />All environments</Link>
              <Link href="/audit" className="pop-action" role="menuitem" onClick={() => close(false)}><Icon name="audit" size={15} />Audit log</Link>
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
        <button type="button" className="ctx-btn env" title="Switch environment" {...props}>
          <Icon name="layers" size={14} className="faint" />
          <span className="ctx-name">{label}</span>
          {current?.is_default && <span className="ctx-tag">default</span>}
          {current?.preview_source && <span className="ctx-tag warn">preview</span>}
          <Icon name="chevronsUpDown" size={14} className="faint" />
        </button>
      )}
    >
      {({ close }) => (
        <>
          <div className="pop-head">
            <span className="eyebrow">Environments</span>
            <span className="faint small">{siblings.length}</span>
          </div>
          {siblings.length > 6 && <SearchField value={query} onChange={setQuery} placeholder="Find an environment…" />}
          <div className="pop-list" role="listbox" aria-label="Environments">
            {shown.map((e) => (
              <Link key={e.name} href={envHref(e.name, section || "overview")} role="option" aria-selected={e.name === env} className={`pop-item ${e.name === env ? "current" : ""}`} onClick={() => close(false)}>
                <Icon name="layers" size={15} className="faint" />
                <span className="grow"><b>{e.slug || e.name}</b><small>{e.preview_source ? `Preview of ${e.preview_source}` : e.version ? `Definitions v${e.version}` : "No definitions yet"}</small></span>
                {e.is_default && <span className="ctx-tag">default</span>}
                {e.name === env && <Icon name="check" size={14} />}
              </Link>
            ))}
            {shown.length === 0 && <p className="pop-empty">No environment matches “{query}”.</p>}
          </div>
          <div className="pop-foot">
            <button type="button" className="pop-action" role="menuitem" onClick={() => { close(false); onCreate(); }}><Icon name="plus" size={15} />Create environment</button>
            <button type="button" className="pop-action" role="menuitem" onClick={() => { close(false); onHistory(); }}><Icon name="gitBranch" size={15} />Branches and history</button>
            <Link href={envHref(env, "settings")} className="pop-action" role="menuitem" onClick={() => close(false)}><Icon name="settings" size={15} />Environment settings</Link>
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
      trigger={(props) => <button type="button" className="appbar-btn" title="Create something" {...props}><Icon name="plus" size={14} /><span className="new-label">New</span></button>}
    >
      {({ close }) => (
        <>
          <div className="pop-head"><span className="eyebrow">Create in {env}</span></div>
          <div className="pop-list">
            {CREATE.map(([key, label, hint, icon, path]) => (
              <Link key={key} role="menuitem" className="pop-item" href={path ? `/envs/${env}${path}` : `${envHref(env, key)}?new=1`} onClick={() => close(false)}>
                <Icon name={icon} size={15} className="faint" />
                <span className="grow"><b>{label}</b><small>{hint}</small></span>
              </Link>
            ))}
          </div>
        </>
      )}
    </Popover>
  );
}

/** What the platform says needs attention right now: the same findings the Observability overview shows. */
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
        <button type="button" className="appbar-icon advisor" title={findings.length ? `${findings.length} finding${findings.length === 1 ? "" : "s"} need attention` : "Advisor: nothing needs attention"} aria-label="Advisor" {...props}>
          <Icon name="bell" size={16} />
          {findings.length > 0 && <span className={`appbar-count ${critical ? "critical" : ""}`}>{findings.length}</span>}
        </button>
      )}
    >
      {({ close }) => (
        <>
          <div className="pop-head">
            <span className="eyebrow">Advisor</span>
            <span className="faint small">last hour</span>
          </div>
          {system.loading && !system.data ? <p className="pop-empty">Checking…</p> : findings.length === 0 ? (
            <div className="pop-clear"><Icon name="check" size={16} /><span><b>All clear</b><small>Requests, functions, jobs, flows, webhooks and mail are healthy, and nothing is close to a limit.</small></span></div>
          ) : (
            <div className="pop-list">
              {findings.map((f, i) => (
                <Link key={i} role="menuitem" className="pop-item finding" href={envHref(env, f.page || "observability")} onClick={() => close(false)}>
                  <span className={`sev ${f.severity}`} />
                  <span className="grow"><b>{f.title}</b><small>{f.message}</small></span>
                </Link>
              ))}
            </div>
          )}
          <div className="pop-foot"><Link href={envHref(env, "observability")} className="pop-action" role="menuitem" onClick={() => close(false)}><Icon name="observability" size={15} />Open observability</Link></div>
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
      trigger={(props) => <button type="button" className="appbar-icon" title="Help" aria-label="Help" {...props}><Icon name="help" size={16} /></button>}
    >
      {({ close }) => (
        <>
          <div className="pop-list">
            {env && <button type="button" role="menuitem" className="pop-item" onClick={() => { close(false); startTour(resume ? { step: seen.step } : {}); }}><Icon name="sparkle" size={15} className="faint" /><span className="grow"><b>{resume ? "Resume the tour" : "Take the tour"}</b><small>{resume ? `Pick up at step ${seen.step + 1}` : "A guided walk through Studio"}</small></span></button>}
            {env && <a role="menuitem" className="pop-item" href={`/envs/${env}/api-docs`} target="_blank" rel="noreferrer" onClick={() => close(false)}><Icon name="book" size={15} className="faint" /><span className="grow"><b>API reference</b><small>This environment's live OpenAPI docs</small></span><Icon name="external" size={13} className="faint" /></a>}
            {!confined && <Link role="menuitem" className="pop-item" href="/audit" onClick={() => close(false)}><Icon name="audit" size={15} className="faint" /><span className="grow"><b>Audit log</b><small>Who changed what</small></span></Link>}
          </div>
          <div className="pop-head"><span className="eyebrow">Keyboard</span></div>
          <dl className="pop-keys">
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
    <header className="appbar">
      <button type="button" className="appbar-icon menu-btn" onClick={onMenu} aria-label="Open navigation"><Icon name="menu" size={16} /></button>
      <Link href="/" className="appbar-brand" title="Pawabase Studio">
        <LogoMark size={24} />
        <span className="appbar-wordmark">pawabase</span>
        <span className="appbar-studio">Studio</span>
      </Link>
      <nav className="appbar-context" aria-label="Location">
        <ProjectMenu runtime={runtime} envs={envs} env={env} section={section} confined={confined} />
        {env && <span className="ctx-sep">/</span>}
        <EnvSwitcher runtime={runtime} envs={envs} env={env} section={section} onCreate={onCreateEnv} onHistory={onHistory} />
        {crumbs.map((c, i) => <span key={i} className="appbar-crumb"><span className="ctx-sep">/</span>{c}</span>)}
      </nav>
      <span className="appbar-spacer" />
      <span data-tour="search" className="tour-wrap top-search"><CommandSearch /></span>
      <NewMenu env={env} />
      <div className="appbar-icons">
        {env && <button type="button" className="appbar-icon" data-tour="history" onClick={onHistory} title="Branches and history" aria-label="Branches and history"><Icon name="gitBranch" size={16} /></button>}
        {env && <button type="button" className="appbar-icon" onClick={() => window.dispatchEvent(new CustomEvent("pawabase:console"))} title="Terminal (⌘J)" aria-label="Toggle the terminal"><Icon name="terminal" size={16} /></button>}
        <Advisor env={env} />
        <HelpMenu env={env} confined={confined} />
      </div>
      <StatusLights />
    </header>
  );
}
