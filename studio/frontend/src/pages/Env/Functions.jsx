import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Field, Json, JsonInput, Loading, Modal, PageHead, Table, useAction } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

export default function Functions({ project, env }) {
  const base = envPath(project.ref, env, "/functions");
  const list = useApi(base);
  const [invoking, setInvoking] = useState(null);
  const [run, busy] = useAction();
  return (
    <Layout title="Functions">
      <PageHead
        title="Functions"
        description={<>Python in <code>projects/{project.ref}/functions/*.py</code>, registered with <code>@function</code>. Call them from routes, flows, schedules and events, or at <code>/functions/v1/&lt;name&gt;</code>.</>}
        actions={<Button disabled={busy} onClick={async () => { if (await run(() => post(`/projects/${project.ref}/code/reload`), "Code reloaded")) list.reload(); }}>Reload code</Button>}
      />
      <Loading state={list}>
        {(data) => (
          <div className="stack lg">
            {data.errors?.length > 0 && <div className="alert error"><b>Code failed to load</b><pre>{data.errors.join("\n")}</pre></div>}
            <Card flush>
              <Table
                rows={data.data}
                empty="No functions loaded. Add a module under functions/ and reload."
                onRowClick={setInvoking}
                columns={[
                  { label: "Name", render: (f) => <b>{f.name}</b> },
                  { label: "Description", key: "description" },
                  { label: "Policy", render: (f) => <code>{JSON.stringify(f.policy ?? null)}</code> },
                  { label: "Timeout", render: (f) => (f.timeout ? `${f.timeout}s` : "—") },
                ]}
              />
            </Card>
            <Card title="Loaded modules">
              <div className="row wrap">{(data.modules || []).map((m) => <Badge key={m}>{m}</Badge>)}{data.router && <Badge tone="blue">routes.py router</Badge>}</div>
            </Card>
            <Card title="Example">
              <pre className="code-block">{`# projects/${project.ref}/functions/orders.py
from pawabase_core.functions import FunctionContext, function

@function("order_total", policy="authenticated")
async def order_total(ctx: FunctionContext):
    rows = await ctx.runtime.query("orders", filters={"id": ctx.input["id"]})
    return {"total": sum(line["price"] for line in rows[0]["lines"])}`}</pre>
            </Card>
          </div>
        )}
      </Loading>
      {invoking && <Invoke base={base} fn={invoking} onClose={() => setInvoking(null)} />}
    </Layout>
  );
}

function Invoke({ base, fn, onClose }) {
  const [input, setInput] = useState({});
  const [asUser, setAsUser] = useState(null);
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  return (
    <Modal wide title={`Invoke ${fn.name}`} onClose={onClose} footer={<Button variant="primary" disabled={busy} onClick={async () => { const r = await run(() => post(`${base}/${fn.name}/invoke`, { input, as_user: asUser })); if (r) setResult(r); }}>Invoke</Button>}>
      <Field label="Input"><JsonInput value={input} onChange={setInput} rows={8} /></Field>
      <Field label="As user (optional)" hint="An auth context; empty invokes with service rights."><JsonInput value={asUser ?? undefined} onChange={setAsUser} rows={3} /></Field>
      {result && <Json value={result} />}
    </Modal>
  );
}
