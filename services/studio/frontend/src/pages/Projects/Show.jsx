import { Link, router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, Modal, PageHead, Switch, Table, useAction, when } from "../../components/ui";
import { del, get, patch, post } from "../../lib/api";

export default function ProjectShow({ project, envs }) {
  const [modal, setModal] = useState(null);
  const [run, busy] = useAction();
  const reload = () => router.reload();
  const reloadCode = async () => {
    const result = await run(() => post(`/projects/${project.ref}/code/reload`), null);
    if (result) alert(result.errors?.length ? `Loaded with errors:\n${result.errors.join("\n")}` : `Loaded ${result.modules.length} module(s).`);
  };
  return (
    <Layout title={project.name}>
      <PageHead
        title={project.name}
        description={project.description || `Project ${project.ref}`}
        actions={<>
          <Button onClick={reloadCode} disabled={busy}>Reload code</Button>
          <Button onClick={() => setModal("export")}><Icon name="braces" />Export blueprint</Button>
          <Button onClick={() => setModal("edit")}>Edit</Button>
          <Button variant="primary" onClick={() => setModal("env")}>New environment</Button>
        </>}
      />
      <Card flush title="Environments" actions={<Button size="sm" onClick={() => setModal("promote")}>Promote…</Button>}>
        <Table
          rows={envs}
          onRowClick={(e) => router.visit(`/projects/${project.ref}/${e.name}`)}
          columns={[
            { label: "Name", render: (e) => <span className="row"><b>{e.name}</b>{e.is_default && <Badge tone="green">default</Badge>}</span> },
            { label: "Version", key: "version" },
            { label: "Created", render: (e) => when(e.created_at) },
            { label: "", render: (e) => <Link onClick={(ev) => ev.stopPropagation()} href={`/projects/${project.ref}/${e.name}/settings`} className="btn sm">Settings</Link> },
          ]}
        />
      </Card>
      <div style={{ marginTop: 20 }}>
        <Card title="Danger zone">
          <div className="spread">
            <span className="muted">Deleting a project removes its definitions, keys and secrets. Data in your own databases is left alone.</span>
            <Button variant="danger" onClick={async () => {
              if (prompt(`Type ${project.ref} to delete the project`) !== project.ref) return;
              if (await run(() => del(`/projects/${project.ref}`), "Project deleted")) router.visit("/");
            }}>Delete project</Button>
          </div>
        </Card>
      </div>
      {modal === "env" && <NewEnvironment project={project} envs={envs} onClose={() => setModal(null)} onDone={reload} />}
      {modal === "edit" && <EditProject project={project} onClose={() => setModal(null)} onDone={reload} />}
      {modal === "promote" && <Promote project={project} envs={envs} onClose={() => setModal(null)} />}
      {modal === "export" && <ExportBlueprint project={project} envs={envs} onClose={() => setModal(null)} />}
    </Layout>
  );
}

function NewEnvironment({ project, envs, onClose, onDone }) {
  const [data, setData] = useState({ name: "", copy_from: "" });
  const [run, busy] = useAction();
  return (
    <Modal title="New environment" onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.name} onClick={async () => {
      if (await run(() => post(`/projects/${project.ref}/envs`, { name: data.name, copy_from: data.copy_from || null }), "Environment created")) { onClose(); onDone(); }
    }}>Create</Button>}>
      <Field label="Name"><input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} placeholder="preview" /></Field>
      <Field label="Copy definitions from">
        <select value={data.copy_from} onChange={(e) => setData({ ...data, copy_from: e.target.value })}>
          <option value="">Start empty</option>
          {envs.map((e) => <option key={e.name}>{e.name}</option>)}
        </select>
      </Field>
    </Modal>
  );
}

function EditProject({ project, onClose, onDone }) {
  const [data, setData] = useState({ name: project.name, description: project.description || "" });
  const [run, busy] = useAction();
  return (
    <Modal title="Edit project" onClose={onClose} footer={<Button variant="primary" disabled={busy} onClick={async () => {
      if (await run(() => patch(`/projects/${project.ref}`, data), "Saved")) { onClose(); onDone(); }
    }}>Save</Button>}>
      <Field label="Name"><input value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} /></Field>
      <Field label="Description"><input value={data.description} onChange={(e) => setData({ ...data, description: e.target.value })} /></Field>
    </Modal>
  );
}

const KINDS = ["schemas", "transformers", "policies", "resources", "routes", "flows", "buckets", "mail-templates", "subscriptions", "webhooks", "inbound-hooks", "schedules"];

function Promote({ project, envs, onClose }) {
  const [from, setFrom] = useState(envs[0]?.name || "");
  const [to, setTo] = useState(envs[1]?.name || "");
  const [include, setInclude] = useState(KINDS);
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  return (
    <Modal wide title="Promote definitions" onClose={onClose} footer={<Button variant="primary" disabled={busy || !from || !to || from === to} onClick={async () => {
      const r = await run(() => post(`/projects/${project.ref}/envs/${from}/promote`, { to, include }), "Promoted");
      if (r) setResult(r);
    }}>Promote {from} → {to}</Button>}>
      <p className="muted" style={{ margin: 0 }}>Copies definitions between environments. Secrets, keys and data are never copied.</p>
      <div className="row">
        <Field label="From"><select value={from} onChange={(e) => setFrom(e.target.value)}>{envs.map((e) => <option key={e.name}>{e.name}</option>)}</select></Field>
        <Field label="To"><select value={to} onChange={(e) => setTo(e.target.value)}>{envs.map((e) => <option key={e.name}>{e.name}</option>)}</select></Field>
      </div>
      <div className="row wrap">
        {KINDS.map((k) => (
          <label key={k} className="check">
            <input type="checkbox" checked={include.includes(k)} onChange={(e) => setInclude(e.target.checked ? [...include, k] : include.filter((x) => x !== k))} /> {k}
          </label>
        ))}
      </div>
      {result && <pre className="code-block">{JSON.stringify(result, null, 2)}</pre>}
    </Modal>
  );
}

function ExportBlueprint({ project, envs, onClose }) {
  const [env, setEnv] = useState(envs.find((e) => e.is_default)?.name || envs[0]?.name || "");
  const [withData, setWithData] = useState(false);
  const [maxRows, setMaxRows] = useState(200);
  const [run, busy] = useAction();
  const download = async () => {
    const doc = await run(
      () => get(`/projects/${project.ref}/envs/${env}/blueprint`, { params: { data: withData, max_rows: maxRows } }),
      "Blueprint downloaded",
    );
    if (!doc) return;
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${project.ref}-${env}.blueprint.json`;
    link.click();
    URL.revokeObjectURL(link.href);
    onClose();
  };
  return (
    <Modal title="Export a blueprint" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !env} onClick={download}><Icon name="braces" />Download JSON</Button></>}>
      <p className="muted" style={{ margin: 0 }}>
        A blueprint is this system as one JSON file: resources, policies, flows, routes, schemas, transformers, buckets,
        mail templates, subscriptions, webhooks, inbound hooks, schedules and roles. Share it, and anyone can create a
        new project from it. A blueprint can only <b>create</b> projects; it never changes a running one.
      </p>
      <Field label="Environment">
        <select value={env} onChange={(e) => setEnv(e.target.value)}>
          {envs.map((e) => <option key={e.name} value={e.name}>{e.name}</option>)}
        </select>
      </Field>
      <Switch checked={withData} onChange={setWithData} label="Include sample data" hint="Records from each resource, so the new project starts with realistic content." />
      {withData && (
        <Field label="Rows per resource" hint="Up to 5,000.">
          <input type="number" min="1" max="5000" value={maxRows} onChange={(e) => setMaxRows(Number(e.target.value))} style={{ maxWidth: 160 }} />
        </Field>
      )}
      <div className="alert info">Never included: secrets, API keys, webhook signing secrets, OAuth credentials, database/storage/mail settings, users.</div>
      {withData && <div className="alert warn">Sample data leaves with the file. Don't include personal data you aren't allowed to share.</div>}
    </Modal>
  );
}
