import { Link } from "@inertiajs/react";
import { useState } from "react";
import Layout, { envHref, sectionNav, useSection } from "../../components/Layout";
import { Bans, FirewallEvents, FirewallOverview, FirewallRules, FirewallTester, ManagedRules } from "./firewall/Firewall";
import { Badge, Button, Card, CopyText, EmptyState, Field, Loading, Modal, PageHead, Switch, Table, TagInput, when, useAction } from "../../components/ui";
import { del, envPath, patch, post, put, useApi } from "../../lib/api";

const STATUS_TONES = { verified: "green", pending: "yellow", failed: "red" };

export default function Networking({ env }) {
  const base = envPath(env);
  const [tab, setTab] = useSection(["domains", "overview", "rules", "managed", "bans", "events", "test"], "overview");
  const [banning, setBanning] = useState(null);
  const ban = (ip) => { setBanning(ip); setTab("bans"); };
  return (
    <Layout title="Networking" subnav={sectionNav("Networking", [
      { title: "Firewall", items: [
        ["overview", "Overview", "What it blocked and who"],
        ["rules", "Rules", "Allow, block, log, rate limit"],
        ["managed", "Managed rules", "Attack signatures, automatic bans"],
        ["bans", "IP bans", "Addresses refused now"],
        ["events", "Events", "Every request it acted on"],
        ["test", "Test a request", "See what would happen"],
      ] },
      { title: "Addresses", items: [["domains", "Custom domains", "Where this environment answers"]] },
    ], tab, setTab)}>
      {tab === "overview" && <FirewallOverview base={base} onBan={ban} onSection={setTab} />}
      {tab === "rules" && <FirewallRules base={base} />}
      {tab === "managed" && <ManagedRules base={base} />}
      {tab === "bans" && <Bans base={base} prefill={banning} onPrefilled={() => setBanning(null)} />}
      {tab === "events" && <FirewallEvents base={base} onBan={ban} />}
      {tab === "test" && <FirewallTester base={base} />}
      {tab === "domains" && (
        <div className="stack lg">
          <PageHead title="Custom domains" description="Addresses this environment answers on, proved with a DNS record." />
          <Domains base={base} />
          <Card title="Other network controls">
            <p className="muted">
              Allowed origins (CORS), the secret-key IP allowlist and maintenance mode live in <Link href={envHref(env, "settings")}>Settings</Link>. Each API key can also be limited to IP ranges and routes under <Link href={envHref(env, "keys")}>API keys</Link>.
            </p>
          </Card>
        </div>
      )}
    </Layout>
  );
}

function Domains({ base }) {
  const list = useApi(`${base}/domains`);
  const [adding, setAdding] = useState(false);
  const [run, busy] = useAction();
  return (
    <Card title="Custom domains" actions={<Button variant="primary" onClick={() => setAdding(true)}>Add domain</Button>} flush>
      <Loading state={list} empty={<EmptyState icon="link" title="No custom domains" action={<Button variant="primary" onClick={() => setAdding(true)}>Add domain</Button>}>Serve this environment's API from your own hostname, such as api.example.com.</EmptyState>}>
        {(data) => (
          <Table
            rows={data.data}
            columns={[
              { label: "Hostname", render: (d) => <code>{d.hostname}</code> },
              { label: "Status", render: (d) => <Badge tone={STATUS_TONES[d.status]}>{d.status}</Badge> },
              { label: "DNS record", render: (d) => d.status === "verified" ? <span className="muted small">verified {when(d.verified_at)}</span> : (
                <div className="stack" style={{ gap: 4 }}>
                  <span className="small muted">Add a {d.record.type} record named</span>
                  <CopyText text={d.record.name} />
                  <span className="small muted">with the value</span>
                  <CopyText text={d.record.value} />
                  {d.last_error && <span className="small" style={{ color: "var(--red, #c4453a)" }}>{d.last_error}</span>}
                </div>
              ) },
              { label: "", render: (d) => <div className="row">
                <Button size="sm" disabled={busy} onClick={async () => { if (await run(() => post(`${base}/domains/${d.id}/verify`))) list.reload(); }}>{d.status === "verified" ? "Re-check" : "Check now"}</Button>
                <Button size="sm" variant="danger" onClick={async () => { if (confirm(`Remove ${d.hostname}?`) && await run(() => del(`${base}/domains/${d.id}`), "Removed")) list.reload(); }}>Remove</Button>
              </div> },
            ]}
          />
        )}
      </Loading>
      <p className="hint" style={{ padding: "12px 16px" }}>
        Verifying proves you own the hostname. Once verified, point it at your gateway with a CNAME or A record. Pawabase does not issue certificates: terminate TLS for the hostname at the proxy or CDN in front of the gateway.
      </p>
      {adding && <AddDomain base={base} onClose={() => setAdding(false)} onDone={() => { setAdding(false); list.reload(); }} />}
    </Card>
  );
}

function AddDomain({ base, onClose, onDone }) {
  const [hostname, setHostname] = useState("");
  const [run, busy] = useAction();
  return (
    <Modal title="Add a domain" onClose={onClose} footer={<Button variant="primary" disabled={busy || !hostname} onClick={async () => { if (await run(() => post(`${base}/domains`, { hostname }), "Domain added: add the DNS record to verify it")) onDone(); }}>Add</Button>}>
      <Field label="Hostname" hint="Without https://, like api.example.com"><input autoFocus value={hostname} onChange={(e) => setHostname(e.target.value)} placeholder="api.example.com" /></Field>
    </Modal>
  );
}

const MATCH_LABELS = { ips: "IPs", paths: "Paths", methods: "Methods", user_agents: "User agents" };
const summary = (match) => Object.entries(match || {}).map(([k, v]) => `${MATCH_LABELS[k] || k}: ${v.join(", ")}`).join(" · ");
