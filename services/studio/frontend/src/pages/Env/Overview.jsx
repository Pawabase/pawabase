import { Link, usePage } from "@inertiajs/react";
import { Fragment } from "react";
import Layout, { envHref } from "../../components/Layout";
import { Card, PageHead } from "../../components/ui";

export default function Overview({ project, env, overview }) {
  const counts = overview.counts || {};
  const day = overview.last_24h || {};
  const infra = overview.infrastructure || {};
  const requests = overview.requests;
  const { gateway_url } = usePage().props;
  return (
    <Layout title={`${project.name} · ${env}`}>
      <PageHead title={`${env}`} description={`Environment ${env} of ${project.name} · definitions version ${overview.version}`} />
      {overview.problems?.length > 0 && (
        <div className="alert warn" style={{ marginBottom: 16 }}>
          <b>Definitions with problems</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{overview.problems.map((p, i) => <li key={i}>{typeof p === "string" ? p : JSON.stringify(p)}</li>)}</ul>
        </div>
      )}
      <div className="grid" style={{ marginBottom: 20 }}>
        {Object.entries(counts).map(([key, value]) => (
          <Link key={key} href={envHref(project.ref, env, key === "buckets" ? "storage" : key)} className="card stat">
            <b>{value}</b><span>{key}</span>
          </Link>
        ))}
      </div>
      <div className="grid wide">
        <Card title="Last 24 hours">
          <dl className="kv">
            <dt>Events</dt><dd>{day.events}</dd>
            <dt>Flow runs</dt><dd>{day.flow_runs} {day.flow_failures > 0 && <span className="badge red">{day.flow_failures} failed</span>}</dd>
            <dt>Jobs</dt><dd>{day.jobs} {day.job_failures > 0 && <span className="badge red">{day.job_failures} failed</span>}</dd>
            <dt>Mail</dt><dd>{day.mail}</dd>
          </dl>
        </Card>
        <Card title="Infrastructure" actions={<Link className="btn sm" href={envHref(project.ref, env, "settings")}>Configure</Link>}>
          <KV entries={Object.entries(infra)} />
        </Card>
        <Card title="Connect">
          <p className="muted" style={{ marginTop: 0 }}>Clients call the gateway with a key from <Link href={envHref(project.ref, env, "keys")} style={{ color: "var(--accent)" }}>API keys</Link>.</p>
          <pre className="code-block">{`curl ${gateway_url}/rest/v1/<resource> \\
  -H "apikey: <publishable key>" \\
  -H "Authorization: Bearer <user access token>"`}</pre>
        </Card>
        {requests && (
          <Card title="API requests (this process)">
            <KV entries={Object.entries(requests).filter(([, v]) => typeof v !== "object")} />
          </Card>
        )}
      </div>
    </Layout>
  );
}

function KV({ entries }) {
  return <dl className="kv">{entries.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>;
}
