import { useMemo, useState } from "react";
import { Badge, Card, Loading, Segmented, Table, when } from "../../../components/ui";
import { envPath, useApi } from "../../../lib/api";
import { MethodBadge, RateBar, ms, percent } from "./shared";

const SORTS = [
  ["errors", "Most failing"],
  ["error_rate", "Error rate"],
  ["slow", "Slowest"],
  ["traffic", "Busiest"],
  ["time", "Most time"],
];

export default function Routes({ env, minutes, onRoute, onTrace }) {
  const base = envPath(env);
  const [sort, setSort] = useState("errors");
  const [search, setSearch] = useState("");
  const state = useApi(`${base}/observability/routes`, { params: { minutes, sort, limit: 200 }, interval: 15000 });
  const routes = useMemo(() => (state.data?.routes || []).filter((r) => r.route.toLowerCase().includes(search.toLowerCase())), [state.data, search]);
  const totals = useMemo(() => {
    const all = state.data?.routes || [];
    const requests = all.reduce((n, r) => n + r.requests, 0);
    const server = all.reduce((n, r) => n + r.server_errors, 0);
    const client = all.reduce((n, r) => n + r.client_errors, 0);
    const slowest = [...all].sort((a, b) => b.p95_ms - a.p95_ms)[0];
    return { requests, server, client, failing: all.filter((r) => r.server_errors > 0).length, slowest };
  }, [state.data]);
  return (
    <div className="stack lg">
      <div className="statstrip">
        <Tile label="Requests" value={totals.requests.toLocaleString()} />
        <Tile label="Server errors (5xx)" value={totals.server.toLocaleString()} tone={totals.server ? "red" : ""} hint={totals.requests ? percent((totals.server / totals.requests) * 100) : null} />
        <Tile label="Client errors (4xx)" value={totals.client.toLocaleString()} hint={totals.requests ? percent((totals.client / totals.requests) * 100) : null} />
        <Tile label="Routes failing" value={totals.failing} tone={totals.failing ? "red" : ""} />
        <Tile label="Slowest route (p95)" value={totals.slowest ? ms(totals.slowest.p95_ms) : "—"} hint={totals.slowest?.route} />
      </div>
      {state.data?.truncated && <div className="alert">This window has more than 50,000 requests; only the newest are analysed. Choose a shorter window for exact numbers.</div>}
      <Card flush title={<div className="row wrap">
        <Segmented options={SORTS} value={sort} onChange={setSort} />
        <input placeholder="Filter routes" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 200 }} />
      </div>}>
        <Loading state={state}>
          {() => <Table rows={routes} empty="No requests in this window." onRowClick={onRoute} columns={[
            { label: "Route", render: (r) => <span className="row"><MethodBadge method={r.method} /><code>{r.route}</code></span> },
            { label: "Requests", render: (r) => <span>{r.requests.toLocaleString()} <span className="faint">· {r.per_minute}/min</span></span> },
            { label: "5xx", render: (r) => (
              <div className="rate-cell">
                <b className={r.server_errors ? "bad" : "faint"}>{r.server_errors}</b>
                <RateBar value={r.error_rate} tone="var(--danger)" />
                <span className="faint">{percent(r.error_rate)}</span>
              </div>
            ) },
            { label: "4xx", render: (r) => (r.client_errors ? <Badge tone="yellow">{r.client_errors}</Badge> : <span className="faint">0</span>) },
            { label: "p50", render: (r) => ms(r.p50_ms) },
            { label: "p95", render: (r) => <b>{ms(r.p95_ms)}</b> },
            { label: "p99", render: (r) => ms(r.p99_ms) },
            { label: "Last failure", render: (r) => (r.last_failure_at ? (
              <a onClick={(e) => { e.stopPropagation(); onTrace(r.last_failure_request_id); }} style={{ cursor: "pointer" }}>{when(r.last_failure_at)}</a>
            ) : <span className="faint">never</span>) },
            { label: "Top problem", render: (r) => (r.top_problems[0] ? <span><code>{r.top_problems[0].problem}</code> <span className="faint">× {r.top_problems[0].count}</span></span> : <span className="faint">—</span>) },
          ]} />}
        </Loading>
      </Card>
    </div>
  );
}

function Tile({ label, value, hint, tone }) {
  return (
    <div>
      <span className={`v ${tone === "red" ? "bad" : ""}`}>{value}</span>
      <span className="l">{label}{hint ? ` · ${hint}` : ""}</span>
    </div>
  );
}
