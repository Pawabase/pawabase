import { Link, router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../components/Layout";
import { Badge, Button, Card, Field, Modal, PageHead, Table, useAction, when } from "../components/ui";
import NewEnvironment from "../components/NewEnvironment";
import { post } from "../lib/api";

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
          <Button variant="primary" onClick={() => setModal("env")}>New environment</Button>
        </>}
      />
      <Card icon="org" flush title="Environments" actions={<Button size="sm" onClick={() => setModal("promote")}>Promote…</Button>}>
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
