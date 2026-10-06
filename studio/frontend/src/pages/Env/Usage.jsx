import Layout from "../../components/Layout";
import { Card, Loading, PageHead, Table } from "../../components/ui";
import { envPath, useApi } from "../../lib/api";

function amount(value, unit = "") {
  return value ? `${Number(value).toLocaleString()}${unit}` : "Unlimited";
}

function reset(seconds) {
  if (!seconds) return "—";
  const hours = Math.ceil(seconds / 3600);
  return hours >= 24 ? `in ${Math.ceil(hours / 24)}d` : `in ${hours}h`;
}

export default function Usage({ env }) {
  const platform = useApi("/usage", { params: { env }, interval: 30000 });
  const gateway = useApi("/usage", { service: "gateway", interval: 15000 });
  const p = platform.data || {};
  const g = gateway.data || {};
  const rows = [
    ["Requests today", g.requests?.daily?.used, g.requests?.daily?.limit, reset(g.requests?.daily?.resets_in)],
    ["Requests this month", g.requests?.monthly?.used, g.requests?.monthly?.limit, reset(g.requests?.monthly?.resets_in)],
    ["Active WebSockets", g.connections?.active, g.connections?.limit, "live"],
    ["Environments", p.environments?.used, p.environments?.limit, "installation"],
    ["API keys", p.api_keys?.used, p.api_keys?.limit, env],
    ["Users", null, p.users?.limit, `${env} · enforced at creation`],
    ["Max upload", null, p.uploads?.limit, "per object"],
  ];
  return <Layout title="Usage & limits" crumbs={["Usage & limits"]}>
    <PageHead title="Usage & limits" description="Live gateway usage and the optional limits configured for this self-hosted installation." />
    <Card title="Current usage" actions={<span className="muted small">Refreshes automatically</span>}>
      <Loading state={{ loading: platform.loading || gateway.loading, error: platform.error || gateway.error, data: rows }}>
        {() => <Table rows={rows} columns={[
          { label: "Resource", render: (row) => <b>{row[0]}</b> },
          { label: "Used", render: (row) => row[1] == null ? "—" : Number(row[1]).toLocaleString() },
          { label: "Limit", render: (row) => amount(row[2], row[0] === "Max upload" && row[2] ? " bytes" : "") },
          { label: "Window / scope", render: (row) => row[3] },
        ]} />}
      </Loading>
    </Card>
    <p className="hint" style={{ marginTop: 14 }}>Request quotas are leased by gateway workers, so the displayed count can include a small reserved block. This avoids a Redis or database call for every request.</p>
  </Layout>;
}
