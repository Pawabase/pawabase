import { useState } from "react";
import Layout, { webhooksSubnav } from "../../components/Layout";
import { Button, Card, Json, Loading, Modal, PageHead, Segmented, Status, Table, useAction, when } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

const STATUSES = [["", "All"], ["failed", "Failed"], ["retrying", "Retrying"], ["delivered", "Delivered"], ["pending", "Pending"]];

/** Every outbound webhook delivery: its attempts, when the next retry is due, and sending again (one, or all that failed). */
export default function WebhookDeliveries({ env }) {
  const base = envPath(env);
  const [status, setStatus] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const deliveries = useApi(`${base}/webhook-deliveries`, { params: { status: status || undefined, endpoint: endpoint || undefined, limit: 200 }, interval: 5000 });
  const endpoints = useApi(`${base}/webhooks`);
  const [open, setOpen] = useState(null);
  const [run, busy] = useAction();

  const redeliverOne = async (id) => { if (await run(() => post(`${base}/webhook-deliveries/${id}/redeliver`), "Redelivery queued")) deliveries.reload(); };
  /** Count what would be sent again, ask, then send. */
  const redeliverFailed = async () => {
    const filter = { status: "failed", endpoint: endpoint || undefined };
    const look = await run(() => post(`${base}/webhook-deliveries/redeliver`, { ...filter, dry_run: true }));
    if (!look || look === true) return;
    if (!look.matched) { alert("No failed deliveries match."); return; }
    if (!confirm(`Send ${look.matched} failed deliver${look.matched === 1 ? "y" : "ies"} again${endpoint ? ` to ${endpoint}` : ""}?`)) return;
    if (await run(() => post(`${base}/webhook-deliveries/redeliver`, filter), "Redeliveries queued")) deliveries.reload();
  };

  return (
    <Layout title="Webhook deliveries" subnav={webhooksSubnav(env, "webhook-deliveries")}>
      <PageHead
        title="Deliveries"
        description="Every attempt to deliver an event to an endpoint. Failed attempts are retried on a growing schedule up to the endpoint's attempt limit; then they wait here to be sent again."
        actions={<Button disabled={busy} onClick={redeliverFailed}>Redeliver failed</Button>}
      />
      <Card bleed flush
        title={<Segmented options={STATUSES} value={status} onChange={setStatus} />}
        actions={<select value={endpoint} onChange={(e) => setEndpoint(e.target.value)} style={{ width: 200 }}>
          <option value="">Every endpoint</option>
          {(endpoints.data?.data || []).map((e) => <option key={e.name} value={e.name}>{e.name}</option>)}
        </select>}>
        <Loading state={deliveries} empty="No deliveries yet. They appear when an event an endpoint subscribes to happens.">
          {(data) => (
            <Table rows={data.data} onRowClick={setOpen} empty="No deliveries match." columns={[
              { label: "Endpoint", render: (d) => <b>{d.endpoint}</b> },
              { label: "Event", render: (d) => <code>{d.event}</code> },
              { label: "Status", render: (d) => <Status value={d.status} /> },
              { label: "HTTP", render: (d) => d.response_status ?? "—" },
              { label: "Attempts", key: "attempts" },
              { label: "Next attempt", render: (d) => (d.next_attempt_at ? when(d.next_attempt_at) : <span className="faint text-faint">—</span>) },
              { label: "Created", render: (d) => when(d.created_at) },
              { label: "", render: (d) => <Button size="sm" disabled={busy} onClick={(e) => { e.stopPropagation(); redeliverOne(d.id); }}>Redeliver</Button> },
            ]} />
          )}
        </Loading>
      </Card>
      {open && <Modal wide title={`${open.endpoint} · ${open.event}`} onClose={() => setOpen(null)}><Json value={open} /></Modal>}
    </Layout>
  );
}
