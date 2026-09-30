import { Link, router } from "@inertiajs/react";
import { Fragment, useState } from "react";
import Layout, { atLeast, envTone } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, CopyText, EmptyState, Field, Modal, PageHead, Section, Segmented, Sheet, TagInput, Tile, useAction } from "../../components/ui";
import { post } from "../../lib/api";

const PROJECT_TONES = ["lavender", "peach", "mint", "sky", "butter", "rose"];
const KIND_LABELS = {
  schemas: "Schemas", transformers: "Transformers", policies: "Policies", resources: "Resources",
  "mail-templates": "Mail templates", flows: "Flows", routes: "Routes", buckets: "Buckets",
  subscriptions: "Subscriptions", webhooks: "Webhooks", "inbound-hooks": "Inbound hooks", schedules: "Schedules",
};

export default function ProjectsIndex({ org, projects, overview }) {
  const [creating, setCreating] = useState(false);
  const canCreate = atLeast(org.role, "developer");
  const [created, setCreated] = useState(null);
  return (
    <Layout title="Projects">
      <PageHead
        title="Projects"
        description={`Backends in ${org.name}. Each has its own environments, data, auth and automation.`}
        actions={canCreate && <Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" />New project</Button>}
      />
      <div className="grid" style={{ marginBottom: 20 }}>
        <Tile tone="lavender" icon="projects" value={overview.projects} label="Projects" i={0} />
        <Tile tone="peach" icon="layers" value={overview.environments} label="Environments" i={1} />
        <Tile tone="mint" icon="jobs" value={overview.queue_backend} label="Queue backend" i={2} />
        <Tile tone="butter" icon="events" value={overview.events_backend} label="Event bus" i={3} />
      </div>
      {projects.length === 0 ? (
        <Card>
          <EmptyState icon="bolt" title={canCreate ? "Create your first project" : "No projects yet"} action={canCreate && <Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" />New project</Button>}>
            {canCreate
              ? `It lives in ${org.name} and comes with development, staging and production environments and API keys for each.`
              : `Ask an owner, admin or developer of ${org.name} to create one.`}
          </EmptyState>
        </Card>
      ) : (
        <div className="grid wide">
          {projects.map((p, i) => (
            <Link key={p.ref} href={`/projects/${p.ref}`} className="card stack rise" style={{ gap: 14, "--i": i }}>
              <div className="row" style={{ gap: 12 }}>
                <span className={`avatar pastel ${PROJECT_TONES[i % PROJECT_TONES.length]}`} style={{ width: 44, height: 44, borderRadius: 14, fontSize: 18 }}>{p.name.slice(0, 1).toUpperCase()}</span>
                <div className="grow">
                  <h2>{p.name}</h2>
                  <code className="faint">{p.ref}</code>
                </div>
                <Icon name="chevronRight" className="faint" />
              </div>
              <p className="muted" style={{ margin: 0, minHeight: 21 }}>{p.description || "No description"}</p>
              <div className="row wrap" style={{ gap: 6 }}>{(p.environments || []).map((e, j) => <span key={e} className={`badge pastel ${envTone(e, j)}`}>{e}</span>)}</div>
            </Link>
          ))}
        </div>
      )}
      {creating && <CreateProject org={org} onClose={() => setCreating(false)} onCreated={(result) => { setCreating(false); setCreated(result); }} />}
      {created && <CreatedKeys project={created} onClose={() => { setCreated(null); router.visit(`/projects/${created.ref}`); }} />}
    </Layout>
  );
}

function slugify(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 40);
}

/** Read a blueprint file and check the parts the browser can check. */
function readBlueprint(text) {
  let doc;
  try {
    doc = JSON.parse(text);
  } catch {
    throw new Error("This file isn't valid JSON.");
  }
  if (!doc || doc.format !== "pawabase.blueprint") throw new Error("This isn't a Pawabase blueprint (format should be \"pawabase.blueprint\").");
  if (doc.version > 1) throw new Error(`This blueprint uses version ${doc.version}; this installation reads version 1.`);
  return doc;
}

function BlueprintSummary({ doc }) {
  const defs = Object.entries(doc.definitions || {}).filter(([, items]) => items.length);
  const rows = Object.values(doc.data || {}).reduce((sum, list) => sum + list.length, 0);
  const total = defs.reduce((sum, [, items]) => sum + items.length, 0);
  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="grid" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
        <div className="tile compact pastel lavender"><div className="stack" style={{ gap: 0 }}><b>{total}</b><span>definitions</span></div></div>
        <div className="tile compact pastel mint"><div className="stack" style={{ gap: 0 }}><b>{(doc.roles || []).length}</b><span>roles</span></div></div>
        <div className="tile compact pastel peach"><div className="stack" style={{ gap: 0 }}><b>{rows}</b><span>sample rows</span></div></div>
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        {defs.map(([kind, items]) => <Badge key={kind}>{KIND_LABELS[kind] || kind} · {items.length}</Badge>)}
      </div>
      {doc.source?.project && (
        <p className="hint" style={{ margin: 0 }}>
          Exported from <code>{doc.source.project}/{doc.source.env}</code>
          {doc.exported_at ? ` on ${new Date(doc.exported_at).toLocaleString()}` : ""}.
        </p>
      )}
      <ul className="hint" style={{ margin: 0, paddingLeft: 18 }}>
        <li>Every environment you list gets the same definitions and roles.</li>
        {rows > 0 && <li>Sample data goes into the first environment only.</li>}
        {(doc.definitions?.webhooks || []).length > 0 && <li>Webhooks arrive switched off; review their URLs before enabling.</li>}
        <li>Secrets, API keys, users and infrastructure settings are never part of a blueprint. Set them up after creating.</li>
      </ul>
    </div>
  );
}

function CreateProject({ org, onClose, onCreated }) {
  const [mode, setMode] = useState("blank");
  const [data, setData] = useState({ name: "", ref: "", description: "", environments: ["development", "staging", "production"] });
  const [blueprint, setBlueprint] = useState(null);
  const [fileName, setFileName] = useState("");
  const [fileError, setFileError] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [problem, setProblem] = useState(null);
  const [run, busy] = useAction();

  const load = async (file) => {
    setFileError(null);
    setBlueprint(null);
    setProblem(null);
    setFileName(file?.name || "");
    if (!file) return;
    try {
      const doc = readBlueprint(await file.text());
      setBlueprint(doc);
      setData((d) => ({ ...d, name: d.name || doc.name || "", description: d.description || doc.description || "" }));
    } catch (error) {
      setFileError(error.message);
    }
  };

  const submit = async () => {
    setProblem(null);
    const body = {
      name: data.name,
      ref: data.ref || slugify(data.name),
      org: org.slug,
      description: data.description,
      environments: data.environments,
      ...(mode === "blueprint" ? { blueprint } : {}),
    };
    const result = await run(async () => {
      try {
        return await post("/projects", body);
      } catch (error) {
        const detail = error.body?.detail ?? error.body;
        if (detail && typeof detail === "object" && detail.where) setProblem(detail);
        throw error;
      }
    }, mode === "blueprint" ? "Project built from the blueprint" : "Project created");
    if (result) onCreated(result);
  };

  const ready = data.name && data.environments.length && (mode === "blank" || blueprint);
  return (
    <Sheet
      title="New project"
      subtitle={`Created in ${org.name}, with its own environments, keys, data and automation.`}
      icon="projects"
      tone="lavender"
      onClose={onClose}
      footer={<>
        <span className="grow" />
        {busy && mode === "blueprint" && <span className="hint">Building every environment… this can take a moment.</span>}
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={busy || !ready} onClick={submit}>
          {mode === "blueprint" ? "Create from blueprint" : "Create project"}
        </Button>
      </>}
    >
      <Section title="Start from">
        <Segmented options={[["blank", "A blank project"], ["blueprint", "A blueprint (JSON)"]]} value={mode} onChange={(m) => { setMode(m); setProblem(null); }} />
        {mode === "blueprint" && (
          <>
            <label
              className="card sunken"
              style={{ display: "grid", placeItems: "center", textAlign: "center", padding: 26, cursor: "pointer", border: `2px dashed ${dragging ? "var(--brand)" : "var(--line-2)"}`, borderRadius: 18 }}
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => { e.preventDefault(); setDragging(false); load(e.dataTransfer.files[0]); }}
            >
              <input type="file" accept=".json,application/json" hidden onChange={(e) => load(e.target.files[0])} />
              <span className="stack" style={{ gap: 6, alignItems: "center" }}>
                <Icon name="braces" size={26} />
                <b>{fileName || "Drop a blueprint file here, or click to choose"}</b>
                <span className="hint">A <code>.blueprint.json</code> file exported from any Pawabase project.</span>
              </span>
            </label>
            {fileError && <div className="alert error">{fileError}</div>}
            {blueprint && <BlueprintSummary doc={blueprint} />}
          </>
        )}
      </Section>
      <Section title="Details">
        <div className="form-grid">
          <Field label="Name"><input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} /></Field>
          <Field label="Reference" hint="Lower-case, stable, used in URLs and keys."><input className="mono" placeholder={slugify(data.name)} value={data.ref} onChange={(e) => setData({ ...data, ref: e.target.value })} /></Field>
          <Field label="Description" optional className="span"><input value={data.description} onChange={(e) => setData({ ...data, description: e.target.value })} /></Field>
          <Field label="Environments" className="span" hint="The first is the default. Each gets its own keys, data and definitions."><TagInput value={data.environments} onChange={(environments) => setData({ ...data, environments })} suggestions={["preview", "testing"]} /></Field>
        </div>
      </Section>
      {problem && (
        <div className="alert error stack" style={{ gap: 4 }}>
          <b>{problem.message}</b>
          <span><code>{problem.where}</code>: {problem.problem}</span>
          <span className="hint" style={{ color: "inherit" }}>Nothing was created. Fix the blueprint and try again.</span>
        </div>
      )}
    </Sheet>
  );
}

function CreatedKeys({ project, onClose }) {
  const report = project.blueprint;
  const secrets = Object.entries(report?.secrets || {});
  return (
    <Modal wide title={`${project.name} is ready`} onClose={onClose} footer={<Button variant="primary" onClick={onClose}>I have saved the keys</Button>}>
      <div className="alert warn">These keys{secrets.length ? " and signing secrets" : ""} are shown once. Publishable keys are safe in browsers; secret keys bypass policies and belong on servers.</div>
      {report && (
        <div className="stack" style={{ gap: 8 }}>
          <h3>Built from the blueprint</h3>
          <div className="row wrap" style={{ gap: 6 }}>
            {Object.entries(report.definitions || {}).map(([kind, n]) => <Badge key={kind} tone="brand">{KIND_LABELS[kind] || kind} · {n}</Badge>)}
            {report.roles > 0 && <Badge tone="green">Roles · {report.roles}</Badge>}
            {Object.values(report.data_rows || {}).reduce((a, b) => a + b, 0) > 0 && <Badge tone="yellow">Sample rows in {report.data_environment}</Badge>}
          </div>
          {(report.warnings || []).map((w) => <div key={w} className="alert info">{w}</div>)}
        </div>
      )}
      {Object.entries(project.keys || {}).map(([env, keys]) => (
        <div key={env} className="stack" style={{ gap: 6 }}>
          <h3>{env}</h3>
          <dl className="kv">
            <dt>publishable</dt><dd><CopyText text={keys.publishable} /></dd>
            <dt>secret</dt><dd><CopyText text={keys.secret} /></dd>
            {(report?.secrets?.[env] ? Object.entries(report.secrets[env]) : []).map(([name, value]) => (
              <Fragment key={name}><dt>{name.split(":")[1]} secret</dt><dd><CopyText text={value.secret} /></dd></Fragment>
            ))}
          </dl>
        </div>
      ))}
    </Modal>
  );
}
