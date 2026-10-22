import { Link } from "@inertiajs/react";
import { useState } from "react";
import Layout, { envHref, sectionNav, useSection } from "../../components/Layout";
import AccessSettings from "../../components/settings/AccessSettings";
import { Bans, FirewallEvents, FirewallOverview, FirewallRules, FirewallTester } from "./firewall/Firewall";
import { Badge, Button, CopyText, EmptyState, Field, Loading, Modal, PageHead, Section, Switch, Table, TagInput, when, useAction } from "../../components/ui";
import { del, envPath, patch, post, put, useApi } from "../../lib/api";

const STATUS_TONES = { verified: "green", pending: "yellow", failed: "red" };

export default function Networking({ env }) {
  const base = envPath(env);
  const [tab, setTab] = useSection(["domains", "access", "overview", "rules", "bans", "events", "test"], "overview");
  const [banning, setBanning] = useState(null);
  const [newRule, setNewRule] = useState(null);
  const ban = (ip) => { setBanning(ip); setTab("bans"); };
  const createRule = (rule) => { setNewRule(rule); setTab("rules"); };
  return (
    <Layout title="Networking" subnav={sectionNav("Networking", [
      { title: "Firewall", items: [
        ["overview", "Overview", null, { icon: "overview" }],
        ["rules", "Rules", null, { icon: "policies" }],
        ["bans", "IP bans", null, { icon: "x" }],
        ["events", "Events", null, { icon: "events" }],
        ["test", "Test a request", null, { icon: "play" }],
      ] },
      { title: "Access", items: [["access", "API access", null, { icon: "keys" }], ["domains", "Custom domains", null, { icon: "link" }]] },
    ], tab, setTab)}>
      {tab === "overview" && <FirewallOverview base={base} onBan={ban} onNewRule={createRule} />}
      {tab === "rules" && <FirewallRules base={base} prefill={newRule} onPrefilled={() => setNewRule(null)} />}
      {tab === "bans" && <Bans base={base} prefill={banning} onPrefilled={() => setBanning(null)} />}
      {tab === "events" && <FirewallEvents base={base} onBan={ban} />}
      {tab === "test" && <FirewallTester base={base} />}
      {tab === "access" && (
        <div className="stack lg">
          <PageHead title="API access" description="Who can call your API and from where, and maintenance mode." />
          <div className="flex flex-col gap-3 max-w-[920px] mx-auto"><AccessSettings env={env} /></div>
        </div>
      )}
      {tab === "domains" && (
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
          <PageHead title="Custom domains" description="Addresses this environment answers on, proved with a DNS record." />
          <div className="flex flex-col gap-3 max-w-[920px] mx-auto">
            <Domains base={base} />
            <Section icon="networking" title="Other network controls">
              <p className="m-0 text-ui-sm text-muted">Allowed websites, the server allowlist for secret keys and maintenance mode are under <button type="button" className="linklike" onClick={() => setTab("access")}>API access</button>. Each API key can also be limited to IP ranges and routes under <Link href={envHref(env, "keys")}>API keys</Link>.</p>
            </Section>
          </div>
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
    <Section icon="link" title="Domains" description="Hostnames this environment answers on. Each one is proved with a DNS record before it is used." actions={<Button variant="primary" onClick={() => setAdding(true)}>Add domain</Button>}>
      <Loading state={list} empty={<EmptyState icon="link" title="No custom domains" action={<Button variant="primary" onClick={() => setAdding(true)}>Add domain</Button>}>Serve this environment's API from your own hostname, such as api.example.com.</EmptyState>}>
        {(data) => (
          <Table
            rows={data.data}
            columns={[
              { label: "Hostname", render: (d) => <code>{d.hostname}</code> },
              { label: "Status", render: (d) => <Badge tone={STATUS_TONES[d.status]}>{d.status}</Badge> },
              { label: "DNS record", render: (d) => d.status === "verified" ? <span className="muted text-muted small text-ui-sm">verified {when(d.verified_at)}</span> : (
                <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 4 }}>
                  <span className="small text-ui-sm muted text-muted">Add a {d.record.type} record named</span>
                  <CopyText text={d.record.name} />
                  <span className="small text-ui-sm muted text-muted">with the value</span>
                  <CopyText text={d.record.value} />
                  {d.last_error && <span className="small text-ui-sm" style={{ color: "var(--red, #c4453a)" }}>{d.last_error}</span>}
                </div>
              ) },
              { label: "", render: (d) => <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                <Button size="sm" disabled={busy} onClick={async () => { if (await run(() => post(`${base}/domains/${d.id}/verify`))) list.reload(); }}>{d.status === "verified" ? "Re-check" : "Check now"}</Button>
                <Button size="sm" variant="danger" onClick={async () => { if (confirm(`Remove ${d.hostname}?`) && await run(() => del(`${base}/domains/${d.id}`), "Removed")) list.reload(); }}>Remove</Button>
              </div> },
            ]}
          />
        )}
      </Loading>
      <p className="hint text-xs font-book text-muted leading-[1.45] m-0">
        Verifying proves you own the hostname. Once verified, point it at your gateway with a CNAME or A record. Pawabase does not issue certificates: terminate TLS for the hostname at the proxy or CDN in front of the gateway.
      </p>
      {adding && <AddDomain base={base} onClose={() => setAdding(false)} onDone={() => { setAdding(false); list.reload(); }} />}
    </Section>
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
