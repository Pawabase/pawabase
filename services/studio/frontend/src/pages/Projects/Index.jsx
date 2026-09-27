import { Link, router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../../components/Layout";
import { Button, Card, CopyText, Field, Modal, PageHead, useAction } from "../../components/ui";
import { post } from "../../lib/api";

export default function ProjectsIndex({ projects, overview }) {
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState(null);
  return (
    <Layout title="Projects">
      <PageHead
        title="Projects"
        description="Each project is a backend with its own environments, data, auth and automation."
        actions={<Button variant="primary" onClick={() => setCreating(true)}>New project</Button>}
      />
      <div className="grid" style={{ marginBottom: 20 }}>
        <Card><div className="stat"><b>{overview.projects}</b><span>projects</span></div></Card>
        <Card><div className="stat"><b>{overview.environments}</b><span>environments</span></div></Card>
        <Card><div className="stat"><b style={{ fontSize: 16 }}>{overview.queue_backend}</b><span>queue</span></div></Card>
        <Card><div className="stat"><b style={{ fontSize: 16 }}>{overview.events_backend}</b><span>events</span></div></Card>
      </div>
      {projects.length === 0 ? (
        <Card>
          <div className="empty stack" style={{ alignItems: "center" }}>
            <h2>Create your first project</h2>
            <p className="muted" style={{ margin: 0 }}>It comes with development, staging and production environments and API keys for each.</p>
            <Button variant="primary" onClick={() => setCreating(true)}>New project</Button>
          </div>
        </Card>
      ) : (
        <div className="grid wide">
          {projects.map((p) => (
            <Link key={p.ref} href={`/projects/${p.ref}`} className="card stack" style={{ gap: 8 }}>
              <div className="spread"><h2>{p.name}</h2><code className="faint">{p.ref}</code></div>
              <p className="muted" style={{ margin: 0, minHeight: 21 }}>{p.description || "No description"}</p>
              <div className="row wrap">{(p.environments || []).map((e) => <span key={e} className="badge">{e}</span>)}</div>
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
  const [data, setData] = useState({ name: "", ref: "", description: "", environments: "development, staging, production" });
  const [run, busy] = useAction();
  const slug = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 40);
  const submit = async () => {
    const result = await run(() => post("/projects", {
      name: data.name,
      ref: data.ref || slug(data.name),
      description: data.description,
      environments: data.environments.split(",").map((s) => s.trim()).filter(Boolean),
    }), "Project created");
    if (result) onCreated(result);
  };
  return (
    <Modal title="New project" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !data.name} onClick={submit}>Create</Button></>}>
      <Field label="Name"><input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} /></Field>
      <Field label="Reference" hint="Lower-case, stable, used in URLs and keys."><input placeholder={slug(data.name)} value={data.ref} onChange={(e) => setData({ ...data, ref: e.target.value })} /></Field>
      <Field label="Description"><input value={data.description} onChange={(e) => setData({ ...data, description: e.target.value })} /></Field>
      <Field label="Environments" hint="Comma-separated. The first is the default."><input value={data.environments} onChange={(e) => setData({ ...data, environments: e.target.value })} /></Field>
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
