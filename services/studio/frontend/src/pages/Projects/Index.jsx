import { Link, router } from "@inertiajs/react";
import { useState } from "react";
import Layout, { envTone } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Button, Card, CopyText, EmptyState, Field, Modal, PageHead, TagInput, Tile, useAction } from "../../components/ui";

const PROJECT_TONES = ["lavender", "peach", "mint", "sky", "butter", "rose"];
import { post } from "../../lib/api";

export default function ProjectsIndex({ projects, overview }) {
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState(null);
  return (
    <Layout title="Projects">
      <PageHead
        title="Projects"
        description="Each project is a backend with its own environments, data, auth and automation."
        actions={<Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" />New project</Button>}
      />
      <div className="grid" style={{ marginBottom: 20 }}>
        <Tile tone="lavender" icon="projects" value={overview.projects} label="Projects" i={0} />
        <Tile tone="peach" icon="layers" value={overview.environments} label="Environments" i={1} />
        <Tile tone="mint" icon="jobs" value={overview.queue_backend} label="Queue backend" i={2} />
        <Tile tone="butter" icon="events" value={overview.events_backend} label="Event bus" i={3} />
      </div>
      {projects.length === 0 ? (
        <Card>
          <EmptyState icon="bolt" title="Create your first project" action={<Button variant="primary" onClick={() => setCreating(true)}><Icon name="plus" />New project</Button>}>
            It comes with development, staging and production environments and API keys for each.
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
      {creating && <CreateProject onClose={() => setCreating(false)} onCreated={(result) => { setCreating(false); setCreated(result); }} />}
      {created && <CreatedKeys project={created} onClose={() => { setCreated(null); router.visit(`/projects/${created.ref}`); }} />}
    </Layout>
  );
}

function CreateProject({ onClose, onCreated }) {
  const [data, setData] = useState({ name: "", ref: "", description: "", environments: ["development", "staging", "production"] });
  const [run, busy] = useAction();
  const slug = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 40);
  const submit = async () => {
    const result = await run(() => post("/projects", {
      name: data.name,
      ref: data.ref || slug(data.name),
      description: data.description,
      environments: data.environments,
    }), "Project created");
    if (result) onCreated(result);
  };
  return (
    <Modal title="New project" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !data.name} onClick={submit}>Create</Button></>}>
      <Field label="Name"><input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} /></Field>
      <Field label="Reference" hint="Lower-case, stable, used in URLs and keys."><input placeholder={slug(data.name)} value={data.ref} onChange={(e) => setData({ ...data, ref: e.target.value })} /></Field>
      <Field label="Description"><input value={data.description} onChange={(e) => setData({ ...data, description: e.target.value })} /></Field>
      <Field label="Environments" hint="The first is the default. Each gets its own keys, data and definitions."><TagInput value={data.environments} onChange={(environments) => setData({ ...data, environments })} suggestions={["preview", "testing"]} /></Field>
    </Modal>
  );
}

function CreatedKeys({ project, onClose }) {
  return (
    <Modal wide title={`${project.name} is ready`} onClose={onClose} footer={<Button variant="primary" onClick={onClose}>I have saved the keys</Button>}>
      <div className="alert warn">These keys are shown once. Publishable keys are safe in browsers; secret keys bypass policies and belong on servers.</div>
      {Object.entries(project.keys || {}).map(([env, keys]) => (
        <div key={env} className="stack" style={{ gap: 6 }}>
          <h3>{env}</h3>
          <dl className="kv">
            <dt>publishable</dt><dd><CopyText text={keys.publishable} /></dd>
            <dt>secret</dt><dd><CopyText text={keys.secret} /></dd>
          </dl>
        </div>
      ))}
    </Modal>
  );
}
