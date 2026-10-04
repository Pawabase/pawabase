import { Link, router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../components/Layout";
import { Icon } from "../components/icons";
import { Badge, Button, Card, Field, Modal, PageHead, Switch, Table, useAction, when } from "../components/ui";
import { get, post } from "../lib/api";

export default function Environments({ runtime, envs }) {
  const [modal, setModal] = useState(null);
  const [run, busy] = useAction();
  const reload = () => router.reload();
  const reloadCode = async () => {
    const result = await run(() => post("/code/reload"), null);
    if (result) alert(result.errors?.length ? `Loaded with errors:\n${result.errors.join("\n")}` : `Loaded ${result.modules.length} module(s).`);
  };
  return (
    <Layout title="Environments">
      <PageHead
        title="Environments"
        description={`${runtime.name} runs as ${envs.length === 1 ? "one environment" : `${envs.length} environments`}. Each has its own definitions, keys, secrets and infrastructure.`}
        actions={<>
          <Button onClick={reloadCode} disabled={busy}>Reload code</Button>
          <Button onClick={() => setModal("export")}><Icon name="braces" />Export blueprint</Button>
          <Button onClick={() => setModal("import")}>Import blueprint</Button>
          <Button variant="primary" onClick={() => setModal("env")}>New environment</Button>
        </>}
      />
      <Card flush title="Environments" actions={<Button size="sm" onClick={() => setModal("promote")}>Promote…</Button>}>
        <Table
          rows={envs}
          onRowClick={(e) => router.visit(`/envs/${e.name}`)}
          columns={[
            { label: "Name", render: (e) => <span className="row"><b>{e.name}</b>{e.is_default && <Badge tone="green">default</Badge>}{e.preview_source && <Badge tone="yellow">preview of {e.preview_source}</Badge>}</span> },
            { label: "Version", key: "version" },
            { label: "Created", render: (e) => when(e.created_at) },
            { label: "", render: (e) => <Link onClick={(ev) => ev.stopPropagation()} href={`/envs/${e.name}/settings`} className="btn sm">Settings</Link> },
          ]}
        />
      </Card>
      {modal === "env" && <NewEnvironment envs={envs} onClose={() => setModal(null)} onDone={reload} />}
      {modal === "promote" && <Promote envs={envs} onClose={() => setModal(null)} />}
      {modal === "export" && <ExportBlueprint runtime={runtime} envs={envs} onClose={() => setModal(null)} />}
      {modal === "import" && <ImportBlueprint onClose={() => setModal(null)} onDone={reload} />}
    </Layout>
  );
}

function NewEnvironment({ envs, onClose, onDone }) {
  const [data, setData] = useState({ name: "", copy_from: "" });
  const [run, busy] = useAction();
  return (
    <Modal title="New environment" onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.name} onClick={async () => {
      if (await run(() => post("/envs", { name: data.name, copy_from: data.copy_from || null }), "Environment created")) { onClose(); onDone(); }
    }}>Create</Button>}>
      <Field label="Name"><input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} placeholder="staging" /></Field>
      <Field label="Copy definitions from">
        <select value={data.copy_from} onChange={(e) => setData({ ...data, copy_from: e.target.value })}>
          <option value="">Start empty</option>
          {envs.map((e) => <option key={e.name}>{e.name}</option>)}
        </select>
      </Field>
    </Modal>
  );
}

const KINDS = ["schemas", "transformers", "policies", "resources", "routes", "flows", "buckets", "mail-templates", "subscriptions", "webhooks", "inbound-hooks", "schedules"];

function Promote({ envs, onClose }) {
  const [from, setFrom] = useState(envs[0]?.name || "");
  const [to, setTo] = useState(envs[1]?.name || "");
  const [include, setInclude] = useState(KINDS);
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  return (
    <Modal wide title="Promote definitions" onClose={onClose} footer={<Button variant="primary" disabled={busy || !from || !to || from === to} onClick={async () => {
      const r = await run(() => post(`/envs/${from}/promote`, { to, include }), "Promoted");
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

function ExportBlueprint({ runtime, envs, onClose }) {
  const [env, setEnv] = useState(envs.find((e) => e.is_default)?.name || envs[0]?.name || "");
  const [withData, setWithData] = useState(false);
  const [maxRows, setMaxRows] = useState(200);
  const [run, busy] = useAction();
  const download = async () => {
    const doc = await run(
      () => get(`/envs/${env}/blueprint`, { params: { data: withData, max_rows: maxRows } }),
      "Blueprint downloaded",
    );
    if (!doc) return;
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${runtime.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${env}.blueprint.json`;
    link.click();
    URL.revokeObjectURL(link.href);
    onClose();
  };
  return (
    <Modal title="Export a blueprint" onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !env} onClick={download}><Icon name="braces" />Download JSON</Button></>}>
      <p className="muted" style={{ margin: 0 }}>
        A blueprint is one environment as a JSON file: resources, policies, flows, routes, schemas, transformers, buckets,
        mail templates, subscriptions, webhooks, inbound hooks, schedules and roles. Share it, and anyone can create new
        environments from it. A blueprint only <b>creates</b> environments; it never changes an existing one.
      </p>
      <Field label="Environment">
        <select value={env} onChange={(e) => setEnv(e.target.value)}>
          {envs.map((e) => <option key={e.name} value={e.name}>{e.name}</option>)}
        </select>
      </Field>
      <Switch checked={withData} onChange={setWithData} label="Include sample data" hint="Records from each resource, so the new environment starts with realistic content." />
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

function ImportBlueprint({ onClose, onDone }) {
  const [text, setText] = useState("");
  const [names, setNames] = useState("development, production");
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  const read = async (file) => file && setText(await file.text());
  return (
    <Modal wide title="Import a blueprint" onClose={onClose} footer={!result ? <Button variant="primary" disabled={busy || !text.trim()} onClick={async () => {
      let blueprint;
      try { blueprint = JSON.parse(text); } catch { alert("That is not valid JSON."); return; }
      const environments = names.split(",").map((n) => n.trim()).filter(Boolean);
      const r = await run(() => post("/blueprints/apply", { blueprint, environments }), "Environments created");
      if (r) { setResult(r); onDone(); }
    }}>Create environments</Button> : <Button onClick={onClose}>Done</Button>}>
      <p className="muted" style={{ margin: 0 }}>Builds new environments from a blueprint. Names that already exist are refused, and a blueprint that fails leaves nothing behind.</p>
      <Field label="Blueprint file"><input type="file" accept="application/json,.json" onChange={(e) => read(e.target.files?.[0])} /></Field>
      <Field label="…or paste it"><textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder='{"format": "pawabase.blueprint", ...}' /></Field>
      <Field label="New environments" hint="Comma separated."><input value={names} onChange={(e) => setNames(e.target.value)} /></Field>
      {result && <><div className="alert warn">Keys are shown once. Copy them now.</div><pre className="code-block">{JSON.stringify(result.keys, null, 2)}</pre></>}
    </Modal>
  );
}
