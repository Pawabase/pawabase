import { Badge, Card, Loading, Table, when } from "../../../components/ui";
import { envPath, useApi } from "../../../lib/api";
import { MethodBadge, statusTone } from "./shared";

export default function Errors({ project, env, minutes, onTrace, onRoute }) {
  const base = envPath(project.ref, env);
  const state = useApi(`${base}/observability/errors`, { params: { minutes }, interval: 15000 });
  return (
    <Card flush title="Failures grouped by what went wrong" actions={state.data ? <span className="faint">{state.data.failures.toLocaleString()} failed requests</span> : null}>
      <Loading state={state}>
        {(d) => <Table rows={d.groups} empty="No failures in this window." onRowClick={(g) => onTrace(g.sample_request_id)} columns={[
          { label: "Status", render: (g) => <Badge tone={statusTone(g.status)}>{g.status}</Badge> },
          { label: "Problem", render: (g) => <div><code>{g.problem}</code>{g.sample_error && g.sample_error !== g.problem && <div className="faint">e.g. {g.sample_error.slice(0, 90)}</div>}</div> },
          { label: "Route", render: (g) => (
            <a onClick={(e) => { e.stopPropagation(); onRoute({ method: g.method, route: g.route }); }} style={{ cursor: "pointer" }}>
              <MethodBadge method={g.method} /> <code>{g.route}</code>
            </a>
          ) },
          { label: "Count", render: (g) => <b>{g.count.toLocaleString()}</b> },
          { label: "Users", key: "users" },
          { label: "First seen", render: (g) => when(g.first_seen) },
          { label: "Last seen", render: (g) => when(g.last_seen) },
        ]} />}
      </Loading>
    </Card>
  );
}
