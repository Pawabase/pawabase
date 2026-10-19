import { Link, router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../components/Layout";
import { Icon } from "../components/icons";
import { Badge, Button, Card, CopyText, Field, Modal, PageHead, Switch, Table, copyToClipboard, useAction, useToast, when } from "../components/ui";
import NewEnvironment from "../components/NewEnvironment";
import { get, post } from "../lib/api";

export default function Environments({ runtime, envs }) {
  const [modal, setModal] = useState(() => (typeof window !== "undefined" && new URLSearchParams(window.location.search).has("new") ? "env" : null));
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
            { label: "Name", render: (e) => <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><b>{e.name}</b>{e.is_default && <Badge tone="green">default</Badge>}{e.preview_source && <Badge tone="yellow">preview of {e.preview_source}</Badge>}</span> },
            { label: "Version", key: "version" },
            { label: "Created", render: (e) => when(e.created_at) },
            { label: "", render: (e) => <Link onClick={(ev) => ev.stopPropagation()} href={`/envs/${e.name}/settings`} className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text sm">Settings</Link> },
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
      <p className="muted text-muted" style={{ margin: 0 }}>Copies definitions between environments. Secrets, keys and data are never copied.</p>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
        <Field label="From"><select value={from} onChange={(e) => setFrom(e.target.value)}>{envs.map((e) => <option key={e.name}>{e.name}</option>)}</select></Field>
        <Field label="To"><select value={to} onChange={(e) => setTo(e.target.value)}>{envs.map((e) => <option key={e.name}>{e.name}</option>)}</select></Field>
      </div>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
        {KINDS.map((k) => (
          <label key={k} className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui">
            <input type="checkbox" checked={include.includes(k)} onChange={(e) => setInclude(e.target.checked ? [...include, k] : include.filter((x) => x !== k))} /> {k}
          </label>
        ))}
      </div>
      {result && <pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg">{JSON.stringify(result, null, 2)}</pre>}
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
      <p className="muted text-muted" style={{ margin: 0 }}>
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
      <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] info">Never included: secrets, API keys, webhook signing secrets, OAuth credentials, database/storage/mail settings, users.</div>
      {withData && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] warn">Sample data leaves with the file. Don't include personal data you aren't allowed to share.</div>}
    </Modal>
  );
}

function ImportBlueprint({ onClose, onDone }) {
  const toast = useToast();
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
      <p className="muted text-muted" style={{ margin: 0 }}>Builds new environments from a blueprint. Names that already exist are refused, and a blueprint that fails leaves nothing behind.</p>
      <Field label="Blueprint file"><input type="file" accept="application/json,.json" onChange={(e) => read(e.target.files?.[0])} /></Field>
      <Field label="…or paste it"><textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder='{"format": "pawabase.blueprint", ...}' /></Field>
      <Field label="New environments" hint="Comma separated."><input value={names} onChange={(e) => setNames(e.target.value)} /></Field>
      {result && <>
        <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] warn">Keys are shown once. Copy them now.</div>
        {Object.entries(result.keys || {}).map(([name, k]) => (
          <div key={name} className="card bg-[color-mix(in_srgb,_var(--surface)_58%,_var(--canvas))] rounded-md p-4 [&.flush]:p-0 [&.flush]:overflow-hidden [&.sunken]:bg-head-bg [&.flush_.card-head_+_*]:[border-top:1px_solid_var(--line)] [a&]:transition-[background_0.12s,_border-color_0.12s] [a&:hover]:transform-[none]! [a&:hover]:shadow-none [border:1px_solid_var(--line)] shadow-none [&_>_.spread:first-child]:mb-3! [&_>_.spread_h2]:text-base [&_>_.spread_h2]:font-semibold [&_>_.spread_h2]:tracking-[-0.01em] [&.flush_>_.card-head_+_*]:border-t-0 [a&:hover]:bg-hover [a&:hover]:border-line-2 [&.flush_tr:last-child_td]:border-b-0 [&_>_.spread_.chips]:gap-0 [&_>_.spread_.chips]:flex-nowrap [&_>_.spread_.chips]:[border:1px_solid_var(--line-2)] [&_>_.spread_.chips]:rounded-sm [&_>_.spread_.chips]:overflow-hidden [&_>_.spread_.chip]:h-[calc(var(--control-sm)_-_2px)] [&_>_.spread_.chip]:p-[0_10px] [&_>_.spread_.chip]:border-0 [&_>_.spread_.chip]:[border-left:1px_solid_var(--line-2)] [&_>_.spread_.chip]:rounded-none [&_>_.spread_.chip]:bg-transparent [&_>_.spread_.chip]:text-muted [&_>_.spread_.chip]:text-xs [&_>_.spread_.chip]:font-medium-plus [&_>_.spread_.chip:first-child]:border-l-0 [&_>_.spread_.chip.active]:bg-press [&_>_.spread_.chip.active]:text-text [&_>_.spread_.chip.on]:bg-press [&_>_.spread_.chip.on]:text-text [&.dimmed]:opacity-70 [&.dimmed:hover]:opacity-100 sunken stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ padding: 14, gap: 10 }}>
            <b>{name}</b>
            <Field label="Publishable key" hint="Safe in browsers and mobile apps."><CopyText text={k.publishable} label={`${name} publishable key`} /></Field>
            <Field label="Secret key" hint="Server-side only. Never ship it to a client."><CopyText text={k.secret} label={`${name} secret key`} /></Field>
          </div>
        ))}
        <Button onClick={async () => { const env = Object.entries(result.keys || {}).map(([name, k]) => `# ${name}\nPAWABASE_${name.toUpperCase().replace(/[^A-Z0-9]/g, "_")}_PUBLISHABLE_KEY=${k.publishable}\nPAWABASE_${name.toUpperCase().replace(/[^A-Z0-9]/g, "_")}_SECRET_KEY=${k.secret}`).join("\n\n"); { const ok = await copyToClipboard(env); toast(ok ? "Copied all keys as .env lines" : "Could not copy", ok ? "ok" : "error"); }; }}><Icon name="copy" />Copy all as .env lines</Button>
      </>}
    </Modal>
  );
}
